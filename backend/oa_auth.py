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

import grp
import json
import logging

from rest_framework import exceptions
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.fields import BooleanField

from django.conf import settings
from django.contrib.auth import authenticate as django_authenticate

def oa_authenticate(request=None, username=None, password=None):
    """AUTHENTICATION
    :type request: django.http.HttpRequest
    """
    # If username + password given, check PAM and our database through authenticate().
    if username is not None and password is not None:
        return django_authenticate(username=username, password=password)

    if request is None:
        return None

    body = request.body  # Django 1.8: request.body will be inaccessible after calling request.POST

    if 'stay_signed_in' in request.POST:
        stay_signed_in = BooleanField().from_native(request.POST['stay_signed_in'])
        request.session.set_expiry(settings.SESSION_COOKIE_AGE if stay_signed_in else 0)

    if "username" in request.POST and "password" in request.POST:
        username = request.POST['username']
        password = request.POST['password']
        return django_authenticate(username=username, password=password)

    # Otherwise, take a look at the REMOTE_USER.
    if "REMOTE_USER" in request.META:
        return django_authenticate(remote_user=request.META["REMOTE_USER"])

    # username + password may also be given as a JSON object.
    try:
        rawjson = json.loads(body)
        username = rawjson['username']
        password = rawjson['password']
        if 'stay_signed_in' in rawjson:
            stay_signed_in = BooleanField().from_native(rawjson['stay_signed_in'])
            request.session.set_expiry(settings.SESSION_COOKIE_AGE if stay_signed_in else 0)
        return django_authenticate(username=username, password=password)
    except (ValueError, KeyError):
        return None


class Unauthorized(Exception):
    pass

def oa_authorize(user):
    # AUTHORIZATION

    # We only allow active staff members to log in.
    if not user.is_active:
        raise Unauthorized('disabled_account')

    # If we have a system user group configured and the user is not a staff member,
    # we may have to grant them staff privileges if they are a member of the
    # configured group.
    if settings.AUTHZ_SYSGROUP and not user.is_staff:
        try:
            sysgroup = grp.getgrnam(settings.AUTHZ_SYSGROUP.encode("utf-8"))
        except KeyError, err:
            logging.error("Failed to query system group '%s': %s", settings.AUTHZ_SYSGROUP, unicode(err))
        else:
            if user.username.lower() in [mem.lower() for mem in sysgroup.gr_mem]:
                logging.warning("User '%s' is a member of system group '%s', granting staff privileges",
                                user.username, settings.AUTHZ_SYSGROUP)
                user.is_staff = True
                user.save()
            else:
                logging.warning("User '%s' is not a member of system group '%s' (Members: %s)",
                                user.username, settings.AUTHZ_SYSGROUP, ', '.join(sysgroup.gr_mem))

    if not user.is_staff:
        raise Unauthorized('unauthorized')


class ExtendedBasicAuthentication(BasicAuthentication):
    """ Basic Authentication that additionally checks authorization the openATTIC way. """

    def authenticate_credentials(self, userid, password):
        user = oa_authenticate(username=userid, password=password)
        if user is None:
            raise exceptions.AuthenticationFailed('Invalid username/password')

        try:
            oa_authorize(user)
        except Unauthorized, err:
            raise exceptions.PermissionDenied(err.args[0])

        return (user, None)

    def authenticate_header(self, request):
        if request.META.get("HTTP_X_REQUESTED_WITH", None) == "XMLHttpRequest":
            # If the client is a web browser running the GUI, do not send back
            # a "Basic" authentication header because it would cause the browser
            # to ask for username/password in its ugly standard dialog, thereby
            # preventing the GUI from displaying something nice.
            return "FormAuthentication"
        return super(ExtendedBasicAuthentication, self).authenticate_header(request)

class RequestAuthentication(BasicAuthentication):
    """ Authenticate using data from the request, be it either username/password
        in POST fields, or a REMOTE_USER set by e.g. Kerberos.
    """

    def authenticate(self, request):
        user = oa_authenticate(request=request)
        if user is None:
            raise exceptions.AuthenticationFailed('Invalid credentials')

        try:
            oa_authorize(user)
        except Unauthorized, err:
            raise exceptions.PermissionDenied(err.args[0])

        return (user, None)


class CsrfExemptSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        if any(map(request.path.startswith, settings.DISABLE_CSRF_FOR_API_PATH)):
            pass  # Disable CSRF validation.
        else:
            return super(CsrfExemptSessionAuthentication, self).enforce_csrf(request)
