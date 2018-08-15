# -*- coding: utf-8 -*-

"""
 *   Copyright (c) 2016 SUSE LLC
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
from utilities import is_executable_installed

if not is_executable_installed('ceph'):
    raise ImportError('ERROR:ceph:Ceph executable couldn\'t be found. The `ceph` package provided '
                      'by your distribution is probably not installed.')

try:
    import rados
except ImportError:
    raise ImportError('ERROR:ceph:`rados` library couldn\'t be found.')
