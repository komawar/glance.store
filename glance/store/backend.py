# Copyright 2010-2011 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging
import sys

from oslo.config import cfg
from stevedore import driver

from glance.store import exceptions
from glance.store.i18n import _
from glance.store import location


LOG = logging.getLogger(__name__)

_DEPRECATED_STORE_OPTS = [
    cfg.DeprecatedOpt('known_stores'),
    cfg.DeprecatedOpt('default_store')
]

_STORE_OPTS = [
    cfg.ListOpt('stores', default=['file', 'http'],
                help=_('List of stores enabled'),
                deprecated_opts=[_DEPRECATED_STORE_OPTS[0]]),
    cfg.StrOpt('default_store', default='file',
               help=_("Default scheme to use to store image data. The "
                      "scheme must be registered by one of the stores "
                      "defined by the 'stores' config option."),
               deprecated_opts=[_DEPRECATED_STORE_OPTS[1]])
]

CONF = cfg.CONF
_STORE_CFG_GROUP = "glance_store"


def _oslo_config_options():
    return ((opt, _STORE_CFG_GROUP) for opt in _STORE_OPTS)


def register_opts(conf):
    for opt, group in _oslo_config_options():
        conf.register_opt(opt, group=group)


def register_store_opts(conf):
    list(_load_stores(conf))


class Indexable(object):
    """Indexable for file-like objs iterators

    Wrapper that allows an iterator or filelike be treated as an indexable
    data structure. This is required in the case where the return value from
    Store.get() is passed to Store.add() when adding a Copy-From image to a
    Store where the client library relies on eventlet GreenSockets, in which
    case the data to be written is indexed over.
    """

    def __init__(self, wrapped, size):
        """
        Initialize the object

        :param wrappped: the wrapped iterator or filelike.
        :param size: the size of data available
        """
        self.wrapped = wrapped
        self.size = int(size) if size else (wrapped.len
                                            if hasattr(wrapped, 'len') else 0)
        self.cursor = 0
        self.chunk = None

    def __iter__(self):
        """
        Delegate iteration to the wrapped instance.
        """
        for self.chunk in self.wrapped:
            yield self.chunk

    def __getitem__(self, i):
        """
        Index into the next chunk (or previous chunk in the case where
        the last data returned was not fully consumed).

        :param i: a slice-to-the-end
        """
        start = i.start if isinstance(i, slice) else i
        if start < self.cursor:
            return self.chunk[(start - self.cursor):]

        self.chunk = self.another()
        if self.chunk:
            self.cursor += len(self.chunk)

        return self.chunk

    def another(self):
        """Implemented by subclasses to return the next element"""
        raise NotImplementedError

    def getvalue(self):
        """
        Return entire string value... used in testing
        """
        return self.wrapped.getvalue()

    def __len__(self):
        """
        Length accessor.
        """
        return self.size


def _load_store(conf, store_entry):
    store_cls = None
    try:
        LOG.debug("Attempting to import store %s", store_entry)
        mgr = driver.DriverManager('glance.store.drivers',
                                   store_entry,
                                   invoke_args=[conf],
                                   invoke_on_load=True)
        return mgr.driver
    except RuntimeError as ex:
        LOG.warn("Failed to load driver %(driver)s."
                 "The driver will be disabled" % dict(driver=driver))


def _load_stores(conf):
    for store_entry in set(conf.glance_store.stores):
        try:
            # FIXME(flaper87): Don't hide BadStoreConfiguration
            # exceptions. These exceptions should be propagated
            # to the user of the library.
            store_instance = _load_store(conf, store_entry)

            if not store_instance:
                continue

            yield (store_entry, store_instance)

        except exceptions.BadStoreConfiguration as e:
            continue


def create_stores(conf=CONF):
    """
    Registers all store modules and all schemes
    from the given config. Duplicates are not re-registered.
    """
    store_count = 0
    store_classes = set()

    for (store_entry, store_instance) in _load_stores(conf):
        schemes = store_instance.get_schemes()
        store_instance.configure()
        if not schemes:
            raise exceptions.BackendException('Unable to register store %s. '
                                              'No schemes associated with it.'
                                              % store_cls)
        else:
            LOG.debug("Registering store %s with schemes %s",
                      store_entry, schemes)

            scheme_map = {}
            for scheme in schemes:
                loc_cls = store_instance.get_store_location_class()
                scheme_map[scheme] = {
                    'store': store_instance,
                    'location_class': loc_cls,
                }
            location.register_scheme_map(scheme_map)
            store_count += 1

    return store_count


def verify_default_store():
    scheme = cfg.CONF.glance_store.default_store
    try:
        get_store_from_scheme(scheme)
    except exceptions.UnknownScheme:
        msg = _("Store for scheme %s not found") % scheme
        raise RuntimeError(msg)


def get_known_schemes():
    """Returns list of known schemes"""
    return location.SCHEME_TO_CLS_MAP.keys()


def get_store_from_scheme(scheme):
    """
    Given a scheme, return the appropriate store object
    for handling that scheme.
    """
    if scheme not in location.SCHEME_TO_CLS_MAP:
        raise exceptions.UnknownScheme(scheme=scheme)
    scheme_info = location.SCHEME_TO_CLS_MAP[scheme]
    return scheme_info['store']


def get_store_from_uri(uri):
    """
    Given a URI, return the store object that would handle
    operations on the URI.

    :param uri: URI to analyze
    """
    scheme = uri[0:uri.find('/') - 1]
    return get_store_from_scheme(scheme)


def get_from_backend(uri, offset=0, chunk_size=None, context=None):
    """Yields chunks of data from backend specified by uri"""

    loc = location.get_location_from_uri(uri)
    store = get_store_from_uri(uri)

    try:
        return store.get(loc, offset=offset,
                         chunk_size=chunk_size,
                         context=context)
    except NotImplementedError:
        raise exceptions.StoreGetNotSupported


def get_size_from_backend(uri, context=None):
    """Retrieves image size from backend specified by uri"""

    loc = location.get_location_from_uri(uri)
    store = get_store_from_uri(uri)

    return store.get_size(loc, context=context)


def delete_from_backend(uri, context=None):
    """Removes chunks of data from backend specified by uri"""
    loc = location.get_location_from_uri(uri)
    store = get_store_from_uri(uri)

    try:
        return store.delete(loc, context=context)
    except NotImplementedError:
        raise exceptions.StoreDeleteNotSupported


def get_store_from_location(uri):
    """
    Given a location (assumed to be a URL), attempt to determine
    the store from the location.  We use here a simple guess that
    the scheme of the parsed URL is the store...

    :param uri: Location to check for the store
    """
    loc = location.get_location_from_uri(uri)
    return loc.store_name


def safe_delete_from_backend(uri, image_id, context=None):
    """Given a uri, delete an image from the store."""
    try:
        return delete_from_backend(uri, context=context)
    except exceptions.NotFound:
        msg = _('Failed to delete image %s in store from URI')
        LOG.warn(msg % image_id)
    except exceptions.StoreDeleteNotSupported as e:
        LOG.warn(str(e))
    except exceptions.UnsupportedBackend:
        exc_type = sys.exc_info()[0].__name__
        msg = (_('Failed to delete image %(image_id)s '
                 'from store (%(exc_type)s)') %
               dict(image_id=image_id, exc_type=exc_type))
        LOG.error(msg)


def _delete_image_from_backend(context, store_api, image_id, uri):
    if CONF.delayed_delete:
        store_api.schedule_delayed_delete_from_backend(context, uri, image_id)
    else:
        store_api.safe_delete_from_backend(context, uri, image_id)


def check_location_metadata(val, key=''):
    if isinstance(val, dict):
        for key in val:
            check_location_metadata(val[key], key=key)
    elif isinstance(val, list):
        ndx = 0
        for v in val:
            check_location_metadata(v, key='%s[%d]' % (key, ndx))
            ndx = ndx + 1
    elif not isinstance(val, unicode):
        raise exceptions.BackendException(_("The image metadata key %(key)s "
                                            "has an invalid type of %(type)s. "
                                            "Only dict, list, and unicode are "
                                            "supported.")
                                          % dict(key=key, type=type(val)))


def store_add_to_backend(image_id, data, size, store, context=None):
    """
    A wrapper around a call to each stores add() method.  This gives glance
    a common place to check the output

    :param image_id:  The image add to which data is added
    :param data: The data to be stored
    :param size: The length of the data in bytes
    :param store: The store to which the data is being added
    :return: The url location of the file,
             the size amount of data,
             the checksum of the data
             the storage systems metadata dictionary for the location
    """
    (location, size, checksum, metadata) = store.add(image_id, data, size)
    if metadata is not None:
        if not isinstance(metadata, dict):
            msg = (_("The storage driver %(driver)s returned invalid "
                     " metadata %(metadata)s. This must be a dictionary type")
                   % dict(driver=str(store), metadata=str(metadata)))
            LOG.error(msg)
            raise exceptions.BackendException(msg)
        try:
            check_location_metadata(metadata)
        except exceptions.BackendException as e:
            e_msg = (_("A bad metadata structure was returned from the "
                       "%(driver)s storage driver: %(metadata)s.  %(e)s.") %
                     dict(driver=str(store), metadata=str(metadata), e=str(e)))
            LOG.error(e_msg)
            raise exceptions.BackendException(e_msg)
    return (location, size, checksum, metadata)


def add_to_backend(conf, image_id, data, size, scheme=None, context=None):
    if scheme is None:
        scheme = conf['glance_store']['default_store']
    store = get_store_from_scheme(scheme)
    try:
        return store_add_to_backend(image_id, data, size, store, context)
    except NotImplementedError:
        raise exceptions.StoreAddNotSupported


def set_acls(location_uri, public=False, read_tenants=[],
             write_tenants=None, context=None):

    if write_tenants is None:
        write_tenants = []

    loc = location.get_location_from_uri(location_uri)
    scheme = get_store_from_location(location_uri)
    store = get_store_from_scheme(scheme)
    try:
        store.set_acls(loc, public=public,
                       read_tenants=read_tenants,
                       write_tenants=write_tenants)
    except NotImplementedError:
        LOG.debug(_("Skipping store.set_acls... not implemented."))
