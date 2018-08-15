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

from django.conf.urls import url, patterns, include
from django.contrib import admin

admin.autodiscover()

from django.conf import settings
from rest.router import ROUTER
from rest_framework.authtoken import views
from module_status import StatusView


def _get_openattic_apps():
    from os.path import commonprefix
    from django.conf import settings
    for name in settings.INSTALLED_APPS:
        try:
            module = __import__(name)
            if commonprefix(
                (settings.PROJECT_ROOT, getattr(module, '__file__'))) == settings.PROJECT_ROOT:
                yield name
        except ImportError, err:
            pass


js_info_dict = {"packages": list(_get_openattic_apps())}

urlpatterns = [
    (r'^api/', include(ROUTER.urls)),
    (r'^api-auth/', include('rest_framework.urls', namespace='rest_framework')),
    (r'^api/api-token-auth$', views.obtain_auth_token),

    # module status service
    (r'^api/status/(?P<module_name>[^/]+)', StatusView.as_view(), {}, 'module_status'),
]

for app in settings.INSTALLED_MODULES:
    try:
        module = __import__(app + ".urls")
    except ImportError:
        pass
    else:
        if hasattr(module.urls, "urlpatterns"):
            urlpatterns += module.urls.urlpatterns

if "rosetta" in settings.INSTALLED_APPS:
    urlpatterns.append((r'rosetta/', include('rosetta.urls')))

# URLs for the GUI
urlpatterns.extend([
    # first, a catch-all URL that serves static files (css, etc)
    url(r'^(?P<path>.+)$', 'django.views.static.serve', {
        'document_root': settings.GUI_ROOT, 'show_indexes': False}),
    # second, a URL that serves index.html for "openattic/"
    url(r'^$', 'django.views.static.serve', {
        'document_root': settings.GUI_ROOT, 'path': 'index.html'})
])

urlpatterns = patterns('', *urlpatterns)
