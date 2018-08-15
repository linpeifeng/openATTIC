# -*- coding: utf-8 -*-
"""
 *  Copyright (C) 2011-2016, it-novum GmbH <community@openattic.org>
 *
 *  openATTIC is free software; you can redistribute it and/or modify it
 *  under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; version 2.
 *
 *  This package is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
"""
import ast
import json
import logging

import django
import itertools
import operator
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelState
from django.db.models.fields.related import ReverseSingleRelatedObjectDescriptor, ForeignObjectRel
from django.db.models.query import QuerySet
from django.core import exceptions
from django.db.models.fields import Field
from django.utils.functional import cached_property

logger = logging.getLogger(__name__)


class NoDbQuery(object):
    def __init__(self, q=None, ordering=None):
        self._q = q
        self._ordering = [] if ordering is None else ordering

    def can_filter(self):
        return True

    def add_q(self, q):
        self._q = q if self._q is None else self._q & q

    def clone(self):
        tmp = NoDbQuery()
        tmp._q = self._q.clone() if self._q is not None else None
        tmp._ordering = self._ordering[:]
        return tmp

    def clear_ordering(self, force_empty=None):
        self._ordering = []

    def add_ordering(self, *keys):
        self._ordering += keys

    @property
    def ordering(self):
        return self._ordering

    @property
    def q(self):
        """:rtype: Q"""
        return self._q

    def set_empty(self):
        self.clear_ordering()
        self._q = None

    def __str__(self):
        return "<NoDbQuery q={}, ordering={}>".format(self._q, self._ordering)


class NodbQuerySet(QuerySet):

    def __init__(self, model=None, using=None, hints=None, request=None, context=None):
        """
        model parameter needs to be optional, as QuerySet.__deepcopy__() sets self.model afterwards.
        """
        self.model = model
        self._context = context
        self._current = 0
        self._query = NoDbQuery()
        self._result_cache = None
        self._prefetch_related_lookups = False

    @cached_property
    def _max(self):
        return len(self._filtered_data) - 1

    def _data(self):
        context = self._context if self._context else NodbManager.nodb_context
        objects = self.model.get_all_objects(context, query=self._query)
        self_pointer = LazyProperty.QuerySetPointer(objects)
        for obj in objects:
            #  Because we're calling the model constructors ourselves, django thinks that
            #  the objects are not in the database. We need to "hack" this.
            obj._state.adding = False
            obj._query_set_pointer = self_pointer

        return objects

    @cached_property
    def _filtered_data(self):
        """
        Each Q child consists of either another Q, `attr__iexact` or `model__attr__iexact` or `attr`
        """
        def _filter_by_modifier(keys, attr, value):
            modifier = keys[1] if len(keys) > 1 else "exact"
            if modifier == "exact":
                return attr == attr.__class__(value)
            elif modifier == "istartswith":
                return attr.startswith(value)
            elif modifier == "icontains":
                return value in attr
            elif modifier == "in":
                return attr in value
            else:
                raise ValueError('Unsupported Modifier {}.'.format(modifier))

        def filter_impl(keys, value, obj):
            assert keys

            if isinstance(obj, dict):
                if keys[0] not in obj:
                    raise AttributeError(
                        'Attribute {} does not exist in dict'.format(keys[0]))
            elif not hasattr(obj, keys[0]):
                raise AttributeError(
                    'Attribute {} does not exist for {}'.format(keys[0], obj.__class__))

            attr = obj[keys[0]] if isinstance(obj, dict) else getattr(obj, keys[0], None)

            if attr is None:
                return value is None
            elif isinstance(attr, list):
                if isinstance(attr[0], basestring):
                    return _filter_by_modifier(keys, attr, value)
                return reduce(operator.or_, [filter_impl(keys[1:], value, e) for e in attr], False)
            elif isinstance(attr, models.Model) or isinstance(attr, dict):
                return filter_impl(keys[1:], value, attr)
            else:
                return _filter_by_modifier(keys, attr, value)

        def filter_one_q(q, obj):
            """
            :type q: Q
            :type obj: NodbModel
            :rtype: bool
            """
            def negate(res):
                return not res if q.negated else res

            if q is None:
                return True
            elif isinstance(q, tuple):
                return filter_impl(q[0].split('__'), q[1], obj)
            elif q.connector == "AND":
                return negate(reduce(lambda l, r: l and filter_one_q(r, obj), q.children, True))
            else:
                children = {c for c in q.children if not isinstance(c, Q) or c.children}
                return negate(reduce(lambda l, r: l or filter_one_q(r, obj), children, False))

        filtered = [obj for obj in self._data()
                    if filter_one_q(self._query.q, obj)]

        for order_key in self.query.ordering[::-1]:
            if order_key.startswith("-"):
                order_key = order_key[1:]
                filtered.sort(key=lambda obj: getattr(obj, order_key), reverse=True)
            else:
                filtered.sort(key=lambda obj: getattr(obj, order_key))

        return filtered

    def __iter__(self):
        self._current = 0
        return self

    def __len__(self):
        return len(self._filtered_data)

    def next(self):
        if self._current > self._max:
            raise StopIteration
        else:
            self._current += 1
            return self._filtered_data[self._current - 1]

    def __getitem__(self, index):
        return self._filtered_data[index]

    def _clone(self, klass=None, setup=False, **kwargs):
        my_clone = NodbQuerySet(self.model, context=self._context)
        my_clone._query = self._query.clone()
        return my_clone

    def __deepcopy__(self, memo):
        return super(NodbQuerySet, self).__deepcopy__(memo)

    def count(self):
        return len(self._filtered_data)

    def get(self, **kwargs):
        """Return a single object filtered by kwargs."""
        filtered_data = self.filter(**kwargs)

        # Thankfully copied from
        # https://github.com/django/django/blob/1.7/django/db/models/query.py#L351
        num = len(filtered_data)
        if num == 1:
            return filtered_data[0]
        if not num:
            raise self.model.DoesNotExist(
                '{} matching query "{}" does not exist.'.format(self.model._meta.object_name,
                                                                filtered_data.query))
        raise self.model.MultipleObjectsReturned(
            "get() returned more than one %s -- it returned %s!" % (
                self.model._meta.object_name,
                num
            )
        )

    def exists(self):
        return bool(self._filtered_data)

    @property
    def query(self):
        return self._query

    def filter(self, *args, **kwargs):
        return super(NodbQuerySet, self).filter(*args, **kwargs)

    def all(self):
        return super(NodbQuerySet, self).all()

    def __str__(self):
        return super(NodbQuerySet, self).__str__()

    def __repr__(self):
        return super(NodbQuerySet, self).__repr__()

    def __nonzero__(self):
        return bool(len(self))

    def iterator(self):
        logger.warning(
            '{}.iterator should only be access when running tests.'.format(self.__class__))
        return []

    _db = not None  # Make Django 1.8 happy.


if django.VERSION[:2] == (1, 6):
    from django.db.models.manager import Manager
    base_manager_class = Manager

elif django.VERSION[:2] == (1, 7):
    from django.db.models.manager import BaseManager
    base_manager_class = BaseManager.from_queryset(NodbQuerySet)

elif django.VERSION[:2] >= (1, 8):
    from django.db.models.manager import BaseManager, Manager

    class base_manager_class(BaseManager.from_queryset(NodbQuerySet), Manager):
        """in DRF 3, rest_framework.relations.RelatedField#get_queryset checks:

        >>> isinstance(queryset, Manager)

        This is unfortunate, but we have to inherent from `Manager`, too!
        """
        pass


class NodbManager(base_manager_class):

    use_for_related_fields = True
    nodb_context = None

    @classmethod
    def set_nodb_context(cls, context):
        cls.nodb_context = context

    def get_queryset(self):
        if django.VERSION[:2] == (1, 6):
            return NodbQuerySet(self.model, using=self._db, context=NodbManager.nodb_context)
        else:
            return self._queryset_class(self.model, using=self._db, hints=self._hints,
                                        context=NodbManager.nodb_context)


class LazyProperty(object):
    """
    See also: django.db.models.query_utils.DeferredAttribute

    Internally used by @bulk_attribute_setter().
    """
    class QuerySetPointer(object):
        def __init__(self, target):
            self.target = target

    def __init__(self, field_name, eval_func, catch_exceptions, field_names):
        self.field_name = field_name
        self.eval_func = eval_func
        self.catch_exceptions = catch_exceptions
        self.field_names = field_names

    def __get__(self, instance, owner=None):
        """
        runs eval_func which fills some lazy properties.
        """
        if hasattr(instance, '_query_set_pointer'):
            query_set = instance._query_set_pointer.target
        else:
            # Fallback. Needed for objects without a queryset.
            query_set = [instance]
        if self.field_name in instance.__dict__:
            return instance.__dict__[self.field_name]

        if self.catch_exceptions is None:
            self.eval_func(instance, query_set, self.field_names)
        else:
            try:
                self.eval_func(instance, query_set, self.field_names)
            except self.catch_exceptions as e:
                logger.exception('failed to populate Field "{}" of {} ({})'
                                 .format(self.field_name, unicode(instance), instance.__class__))
                fields = instance.__class__.make_model_args({}, fields_force_none=self.field_names)
                for field_name, value in fields.items():
                    setattr(instance, field_name, value)

        if self.field_name not in instance.__dict__:
            raise KeyError(
                'LazyProperty: {} did not set {} of {}'.format(self.eval_func, self.field_name,
                                                               instance))
        return instance.__dict__[self.field_name]

    def __set__(self, instance, value):
        """
        Deferred loading attributes can be set normally (which means there will
        never be a database lookup involved.
        """
        instance.__dict__[self.field_name] = value


def bulk_attribute_setter(field_names, catch_exceptions=None):
    """
    The idea @behind bulk_attribute_setter is to delay expensive calls to librados, until someone
    really needs the information gathered in this call. If the attribute is never used, the call
    will never be executed. In general, this is called lazy execution.

    Before, NodbQuerySet called self.model.get_all_objects to generate a list of objects. The
    implementations of get_all_objects were calling the librados commands to fill all attributes,
    even if they were never accessed.

    Because a field may never be accessed, this can generate better performance than caching,
    especially if the cache is cold.

    The bulk_attribute_setter decorator can be used like so:
    >>> class MyModel(NodbModel):
    >>>     my_field = models.IntegerField()
    >>>
    >>>     @bulk_attribute_setter(['my_field'])
    >>>     def set_my_field(self, objs, field_names):
    >>>         self.my_field = 42

    Keep in mind, that you can set the my_field attribute on all objects, not just self.

    The decorator modifies the model to look like this:
    >>> def set_my_field(self, objs):
    >>>     self.my_field = 42
    >>>
    >>> class MyModel(NodbModel):
    >>>     my_field = models.IntegerField()
    >>>     set_my_field = LazyPropertyContributor(['my_field'], set_my_field)

    A LazyPropertyContributor property implements the contribute_to_class method, which modifies
    the model itself to look like so:
    >>> class MyModel(NodbModel):
    >>>     my_field = LazyProperty('my_field', set_my_field)

    The my_field field is not overwritten, because the fields are already moved into the _meta class
    at this point. If someone then accesses the my_field attribute, LazyProperty.__get__ is called,
    which then calls set_my_field to set the field, as if one had written:
    >>> instances = MyModel.objects.all()
    >>> set_my_field(instances[0], instances)
    >>> assert instances[0].my_field == 42

    For example, get_all_objects generates a QuerySet like this:

    id	name	  disk_usage
    0	'foo'     LazyProperty('disk_usage')
    1	'bar'	  LazyProperty('disk_usage')

    When accessing bar.disk_usage, LazyProperty calls `ceph df` and fills the queryset like so:

    id	name	disk_usage
    0	'foo'   1MB
    1	'bar'  	2MB

    :type field_names: list[str]
    :param catch_exceptions: Exceptions that will be caught. In case of an exception, all
        `field_names` will be set to None.
    :type catch_exceptions: exceptions.Exception | tuple[exceptions.Exception]
    """

    if not len(field_names):
        raise ValueError('`field_names` must not be empty.')

    class LazyPropertyContributor(object):
        def __init__(self, field_names, func):
            self.field_names = field_names
            self.func = func

        def contribute_to_class(self, cls, name, virtual_only=False):
            for name in self.field_names:
                setattr(cls, name, LazyProperty(name, self.func, catch_exceptions,
                                                self.field_names))

    def decorator(func):
        return LazyPropertyContributor(field_names, func)

    return decorator


class NodbModel(models.Model):

    objects = NodbManager()

    class Meta:
        # Needs to be true to be able to create the necessary database tables by using Django
        # migrations. The table is necessary to be able to use Django model relations.
        managed = True
        abstract = True

    @staticmethod
    def get_all_objects(context, query):
        msg = 'Every NodbModel must implement its own get_all_objects() method.'
        raise NotImplementedError(msg)

    def get_modified_fields(self, update_fields=None, **kwargs):
        """
        Returns a dict of fields, which have changed. There are two known problems:

        1. There is a race between get_modified_fields and the call to this.save()
        2. A type change, e.g. str and unicode is not handled.

        :param update_fields: restrict the search for updated fields to update_fields.
        :param kwargs: used to retrieve the original. default: `pk`
        :rtype: tuple[dict[str, Any], T <= NodbModel]
        :return: A tuple consisting of the diff and the original model instance
        """
        if not kwargs:
            kwargs['pk'] = self.pk

        field_names = [f.attname for f in self.__class__._meta.fields]
        if update_fields is None:
            update_fields = field_names
        else:
            assert not (set(update_fields) - set(field_names))

        fields = [f for f in self.__class__._meta.fields if f.attname in update_fields]
        original = self.__class__.objects.get(**kwargs)
        return {
            field.attname: getattr(self, field.attname, None)
            for field
            in fields
            if field.editable and getattr(self, field.attname, None) != getattr(original,
                                                                                field.attname, None)
        }, original

    def attribute_is_unevaluated_lazy_property(self, attr):
        """
        :rtype: bool
        """
        if attr not in self.__class__.__dict__:
            return False
        prop = self.__class__.__dict__[attr]
        if not isinstance(prop, LazyProperty):
            return False
        return attr not in self.__dict__

    def set_read_only_fields(self, obj, include_pk=True):
        """
        .. example::
            >>> insert = self.id is None
            >>> diff, original = self.get_modified_fields(name=self.name) if insert
            >>>     else self.get_modified_fields()
            >>> if not insert:
            >>>     self.set_read_only_fields()
        """
        if include_pk:
            self.pk = obj.pk

        for field in self.__class__._meta.fields:
            if (not field.editable
               and not self.attribute_is_unevaluated_lazy_property(field.attname)
               and hasattr(obj, field.attname)
               and getattr(self, field.attname, None) != getattr(obj, field.attname)):
                setattr(self, field.attname, getattr(obj, field.attname))

    @classmethod
    def make_model_args(cls, json_result, fields_force_none=None):
        """
        TODO: fields_force_none could be auto generated by the field names.

        :type json_result: dict[str, Any]
        :type fields_force_none: list[str]
        :rtype: dict[str, Any]
        """
        def get_val_from_json(key):
            if key in json_result:
                return json_result[key]
            elif key.replace('_', '-') in json_result:
                # '-' is not supported for field names, but used by ceph.
                return json_result[key.replace('_', '-')]
            raise AttributeError

        def handle_field(field):
            """:rtype: list[tuple[str, Any]]"""
            try:
                val = get_val_from_json(field.attname)
            except AttributeError:
                return []

            if val is None and not field.null:
                return []

            if isinstance(field, models.ForeignKey):
                return [(field.attname, val)]

            try:
                python_val = field.to_python(val)
            except ValidationError as e:
                return []

            return [(field.attname, python_val)]

        model_args = dict(
            itertools.chain.from_iterable([handle_field(field) for field in cls._meta.fields])
        )
        for name in fields_force_none or []:
            if name not in model_args:
                model_args[name] = None
        return model_args

    def __init__(self, *args, **kwargs):
        # super(NodbModel, self).__init__(*args, **kwargs)
        self._state = ModelState()

        self.__dict__.update(kwargs)

        # We need to trigger the __set__ method of related fields
        for (key, value) in kwargs.iteritems():
            if key in self.__class__.__dict__ and \
                    isinstance(self.__class__.__dict__[key], ReverseSingleRelatedObjectDescriptor):
                setattr(self, key, value)

        for field in self._meta.concrete_fields:
            is_related_field = isinstance(field.rel, ForeignObjectRel)

            # set defaults:
            if not is_related_field \
                    and not self.attribute_is_unevaluated_lazy_property(field.name) \
                    and not hasattr(self, field.name):
                setattr(self, field.name, field.get_default())

    def save(self, force_insert=False, force_update=False, using=None,
             update_fields=None):
        """
        This base implementation does nothing, except telling django that self is now successfully
        inserted.
        """
        self._state.adding = False


class JsonField(Field):
    empty_strings_allowed = False

    def __init__(self, *args, **kwargs):
        """
        :param base_type: list | dict
        :type base_type: type
        :rtype: JsonField[T]
        """
        self.base_type = kwargs['base_type']
        del kwargs['base_type']
        super(JsonField, self).__init__(*args, **kwargs)

    def to_python(self, value):
        """:rtype: T"""
        def check_base_type(val):
            if not isinstance(val, self.base_type):
                raise exceptions.ValidationError(
                    "invalid JSON type. Got {}, expected {}".format(type(parsed), self.base_type),
                    code='invalid',
                    params={'value': value},
                )
            return val

        if value is None:
            return None
        if isinstance(value, self.base_type):
            return value
        if not value and self.null:
            return None
        try:
            parsed = json.loads(value)
            return check_base_type(parsed)
        except (ValueError, TypeError) as _:
            try:
                # Evil hack to support PUT requests to the Browsable API of the
                # django-rest-framework as we cannot determine if restapi.JsonField.tonative() is
                # called for json or for rendering the form.
                obj = ast.literal_eval(value)
                return check_base_type(obj)
            except ValueError:
                raise exceptions.ValidationError(
                    "invalid JSON",
                    code='invalid',
                    params={'value': value},
                )

    def deconstruct(self):
        name, path, args, kwargs = super(JsonField, self).deconstruct()
        kwargs['base_type'] = self.base_type
        return name, path, args, kwargs

    @property
    def empty_values(self):
        return [u'', [], {}]

try:
    from django.db.migrations import AlterField

    class AlterNoDBField(AlterField):
        def database_forwards(self, app_label, schema_editor, from_state, to_state):
            pass

        def describe(self):
            return "Alter NoDB field {} on {}".format(self.name, self.model_name)
except ImportError:
    # Django 1.6 does not have an AlterField, but it also doesn't use the Django 1.7+ migrations, so
    # no need to do anything here.
    pass
