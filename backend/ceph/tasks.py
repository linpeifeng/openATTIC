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
import logging
import time

from exception import ExternalCommandError
from taskqueue.models import task
from ceph import librados

logger = logging.getLogger(__name__)


@task(description='Delete RBD {1}/{2}',
      metadata=lambda fsid, pool_name, image_name: {'fsid': fsid, 'pool': pool_name, 'image': image_name})
def delete_rbd(fsid, pool_name, image_name):
    from ceph.models import fsid_context
    with fsid_context(fsid) as ctx:
        rbd_api = librados.RbdApi(fsid)
        rbd_api.remove(pool_name, image_name)


@task(description='Setting number of PGs to {2}')
def set_pgs(fsid, pool_id, pgs):
    from ceph.models import CephPool, fsid_context

    with fsid_context(fsid) as ctx:
        pool = CephPool.objects.get(id=pool_id)
        api = librados.MonApi(fsid)

        api.osd_pool_set(pool.name, 'pg_num', pgs)
        api.osd_pool_set(pool.name, 'pgp_num', pgs)
        return track_pg_creation(fsid, pool_id, pool.pg_num, pgs)


@task(description='Setting number of PGs to {3}',
      percent=lambda fsid, poolid, before, after, current=None: float(
          (current or 0) - before) / float(after - before) * 100)
def track_pg_creation(fsid, pool_id, pg_count_before, pg_count_after, pgs_current_active=None):
    """
    :type fsid: str
    :type pool_id: int
    :type pg_count_before: int
    :type pg_count_after: int
    :type pgs_current_active: int | None
    """
    from ceph.models import CephPg, CephPool, fsid_context

    with fsid_context(fsid):
        pool = CephPool.objects.get(id=pool_id)
        pgs = list(CephPg.objects.filter(pool_name__exact=pool.name))

        def pg_in_state(state):
            return len([pg for pg in pgs if state in pg.state])

        active = pg_in_state('active')
        creating = pg_in_state('creating')

        logger.info(
            'before={} after={} all={} active={} creating={}'.format(
                pg_count_before, pg_count_after, len(pgs), active, creating))

        if active >= pg_count_after:
            return
        else:
            return track_pg_creation(fsid, pool_id, pg_count_before, pg_count_after, active)


@task(description='Get RBD performance data of cluster \'{0}\', pool \'{1}\' and RBD image \'{2}\'')
def get_rbd_performance_data(fsid, pool_name, image_name):
    start_time = time.time()
    api = librados.RbdApi(fsid)
    try:
        disk_usage = api.image_disk_usage(pool_name, image_name)
    except ExternalCommandError:
        logger.exception('image_disk_usage failed')
        return {}, 0
    exec_time = time.time() - start_time

    return disk_usage, round(exec_time * 1000, 2)
