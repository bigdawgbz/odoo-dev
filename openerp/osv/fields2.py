# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2013 OpenERP (<http://www.openerp.com>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

""" High-level objects for fields. """

from copy import copy
from datetime import date, datetime
from functools import partial
from operator import attrgetter
import logging

from openerp.tools import float_round, ustr, html_sanitize, lazy_property
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT as DATE_FORMAT
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT as DATETIME_FORMAT

DATE_LENGTH = len(date.today().strftime(DATE_FORMAT))
DATETIME_LENGTH = len(datetime.now().strftime(DATETIME_FORMAT))

_logger = logging.getLogger(__name__)


class SpecialValue(object):
    """ Encapsulates a value in the cache in place of a normal value. """
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value

class FailedValue(SpecialValue):
    """ Special value that encapsulates an exception instead of a value. """
    def __init__(self, exception):
        self.exception = exception
    def get(self):
        raise self.exception

def _check_value(value):
    """ Return `value`, or call its getter if `value` is a :class:`SpecialValue`. """
    return value.get() if isinstance(value, SpecialValue) else value


def default(value):
    """ Return a compute function that provides a constant default value. """
    def compute(field, records):
        for record in records:
            record[field.name] = value

    return compute


def compute_related(field, records):
    """ Compute the related `field` on `records`. """
    scope = records._scope
    sudo_scope = scope.sudo()
    for record in records:
        # bypass access rights check when traversing the related path
        value = record.attach_scope(sudo_scope) if record.id else record
        for name in field.related:
            value = value[name]
        # re-scope the resulting value
        if isinstance(value, BaseModel):
            value = value.attach_scope(scope)
        record[field.name] = value

def inverse_related(field, records):
    """ Inverse the related `field` on `records`. """
    for record in records:
        other = record
        for name in field.related[:-1]:
            other = other[name]
        if other:
            other[field.related[-1]] = record[field.name]

def search_related(field, operator, value):
    """ Determine the domain to search on `field`. """
    return [('.'.join(field.related), operator, value)]


class MetaField(type):
    """ Metaclass for field classes. """
    by_type = {}

    def __init__(cls, name, bases, attrs):
        super(MetaField, cls).__init__(name, bases, attrs)
        if cls.type:
            cls.by_type[cls.type] = cls


class Field(object):
    """ Base class of all fields. """
    __metaclass__ = MetaField

    interface_for = None        # the column or field interfaced by self, if any

    name = None                 # name of the field
    type = None                 # type of the field (string)
    relational = False          # whether the field is a relational one
    model_name = None           # name of the model of this field
    comodel_name = None         # name of the model of values (if relational)
    inverse_field = None        # inverse field (object), if it exists

    store = True                # whether the field is stored in database
    depends = ()                # collection of field dependencies
    compute = None              # name of model method that computes value
    inverse = None              # name of model method that inverses field
    search = None               # name of model method that searches on field
    related = None              # sequence of field names, for related fields

    string = None               # field label
    help = None                 # field tooltip
    readonly = False
    required = False
    states = None
    groups = False              # csv list of group xml ids

    def __init__(self, string=None, **kwargs):
        kwargs['string'] = string
        for attr, value in kwargs.iteritems():
            setattr(self, attr, value)

    def reset(self):
        """ Prepare `self` for a new setup. This resets all lazy properties. """
        lazy_property.reset_all(self)
        self.__dict__.pop('setup', None)

    def copy(self, **kwargs):
        """ make a copy of `self`, possibly modified with parameters `kwargs` """
        field = copy(self)
        for attr, value in kwargs.iteritems():
            setattr(field, attr, value)
        # Note: lazy properties will be recomputed later thanks to reset()
        return field

    def set_model_name(self, model_name, name):
        """ assign the model and field names of `self` """
        self.model_name = model_name
        self.name = name
        if not self.string:
            self.string = name.replace('_', ' ').capitalize()

    def __str__(self):
        return "%s.%s" % (self.model_name, self.name)

    def __repr__(self):
        return "%s.%s" % (self.model_name, self.name)

    def get_description(self):
        """ Return a dictionary that describes the field `self`. """
        desc = {'type': self.type, 'store': self.store}
        for attr in dir(self):
            if attr.startswith('_description_'):
                value = getattr(self, attr)
                if value:
                    desc[attr[13:]] = value
        return desc

    # properties used by get_description()
    _description_depends = property(attrgetter('depends'))
    _description_related = property(attrgetter('related'))
    _description_readonly = property(attrgetter('readonly'))
    _description_required = property(attrgetter('required'))
    _description_states = property(attrgetter('states'))
    _description_groups = property(attrgetter('groups'))

    @property
    def _description_string(self):
        if self.string and scope.lang:
            name = "%s,%s" % (self.model_name, self.name)
            trans = scope['ir.translation']._get_source(name, 'field', scope.lang)
            return trans or self.string
        return self.string

    @property
    def _description_help(self):
        if self.help and scope.lang:
            name = "%s,%s" % (self.model_name, self.name)
            trans = scope['ir.translation']._get_source(name, 'help', scope.lang)
            return trans or self.help
        return self.help

    def to_column(self):
        """ return a low-level field object corresponding to `self` """
        assert self.store
        if self.interface_for:
            assert isinstance(self.interface_for, fields._column)
            return self.interface_for

        _logger.debug("Create fields._column for Field %s", self)
        args = {}
        for attr in dir(self):
            if attr.startswith('_column_'):
                args[attr[8:]] = getattr(self, attr)
        return getattr(fields, self.type)(**args)

    # properties used by to_column() to create a column instance
    _column_string = property(attrgetter('string'))
    _column_help = property(attrgetter('help'))
    _column_readonly = property(attrgetter('readonly'))
    _column_required = property(attrgetter('required'))
    _column_states = property(attrgetter('states'))
    _column_groups = property(attrgetter('groups'))

    #
    # Conversion of values
    #

    def null(self):
        """ return the null value for this field """
        return False

    def convert_to_cache(self, value):
        """ convert `value` to the cache level; `value` may come from an
            assignment, or have the format of methods :meth:`BaseModel.read` or
            :meth:`BaseModel.write`
        """
        return value

    def convert_to_read(self, value, use_name_get=True):
        """ convert `value` from the cache to a value as returned by method
            :meth:`BaseModel.read`
        """
        return value

    def convert_to_write(self, value, target=None, fnames=None):
        """ convert `value` from the cache to a valid value for method
            :meth:`BaseModel.write`.

            :param target: optional, the record to be modified with this value
            :param fnames: for relational fields only, an optional collection of
                field names to convert
        """
        return self.convert_to_read(value)

    def convert_to_export(self, value):
        """ convert `value` from the cache to a valid value for export. """
        return bool(value) and ustr(value)

    def convert_to_display_name(self, value):
        """ convert `value` from the cache to a suitable display name. """
        return ustr(value)

    #
    # Getter/setter methods
    #

    def __get__(self, record, owner):
        """ return the value of field `self` on `record` """
        if record is None:
            return self         # the field is accessed through the owner class

        try:
            return record._cache[self]
        except KeyError:
            pass

        # cache miss, retrieve value
        with record._scope:
            if record._id:
                # normal record -> read or compute value for this field
                self.determine_value(record[0])
            elif record:
                # new record -> compute default value for this field
                record.add_default_value(self.name)
            else:
                # null record -> return the null value for this field
                return self.null()

        # the result should be in cache now
        return record._cache[self]

    def __set__(self, record, value):
        """ set the value of field `self` on `record` """
        if not record:
            raise Warning("Null record %s may not be assigned" % record)

        # only one record is updated
        record = record[0]

        with record._scope as _scope:
            # adapt value to the cache level
            value = self.convert_to_cache(value)

            if _scope.draft or not record._id:
                # determine dependent fields
                spec = self.modified_draft(record)

                # set value in cache, inverse field, and mark record as dirty
                record._cache[self] = value
                if _scope.draft:
                    if self.inverse_field:
                        self.inverse_field._update(value, record)
                    record._dirty = True

                # determine more dependent fields, and invalidate them
                if self.relational:
                    spec += self.modified_draft(record)
                _scope.invalidate(spec)

            else:
                # simply write to the database, and update cache
                record.write({self.name: self.convert_to_write(value)})
                record._cache[self] = value

    #
    # Management of the computation of field values.
    #

    @lazy_property
    def _compute_function(self):
        """ Return a function to call with records to compute this field. """
        if isinstance(self.compute, basestring):
            return getattr(type(scope[self.model_name]), self.compute)
        elif callable(self.compute):
            return partial(self.compute, self)
        else:
            raise Warning("No way to compute field %s" % self)

    @lazy_property
    def _compute_one(self):
        """ Test whether the compute function has the decorator ``@one``. """
        from openerp import one
        return getattr(self._compute_function, '_api', None) is one

    def compute_value(self, records, check_exists=False):
        """ Invoke the compute method on `records`. If `check` is ``True``, the
            method filters out non-existing records before computing them.
        """
        # if required, keep new and existing records only
        if check_exists:
            all_recs = records
            new_recs = [rec for rec in records if not rec.id]
            records = sum(new_recs, records.exists())

            # mark non-existing records in cache
            exc = MissingError("Computing a field on non-existing records.")
            (all_recs - records)._cache.update(FailedValue(exc))

        # mark the field failed in cache, so that access before computation
        # raises an exception
        exc = Warning("Field %s is accessed before being computed." % self)
        records._cache[self] = FailedValue(exc)

        self._compute_function(records)

    def determine_value(self, record):
        """ Determine the value of `self` for `record`. """
        if self.store and not (self.compute and scope.draft):
            # recompute field on record if required
            recs_todo = scope.recomputation[self]
            if record in recs_todo:
                # execute the compute method in NON-DRAFT mode, so that assigned
                # fields are written to the database
                if self._compute_one:
                    self.compute_value(record, check_exists=True)
                else:
                    self.compute_value(recs_todo, check_exists=True)
            else:
                record._prefetch_field(self.name)

        else:
            # execute the compute method in DRAFT mode, so that assigned fields
            # are not written to the database
            with record._scope.draft():
                if self._compute_one:
                    self.compute_value(record)
                else:
                    record._in_cache()
                    recs = record._in_cache_without(self.name)
                    self.compute_value(recs, check_exists=True)

    def determine_default(self, record):
        """ determine the default value of field `self` on `record` """
        record._cache[self] = SpecialValue(self.null())
        if self.compute:
            self.compute_value(record)

    def determine_inverse(self, records):
        """ Given the value of `self` on `records`, inverse the computation. """
        if isinstance(self.inverse, basestring):
            getattr(records, self.inverse)()
        elif callable(self.inverse):
            self.inverse(self, records)

    def determine_domain(self, operator, value):
        """ Return a domain representing a condition on `self`. """
        if isinstance(self.search, basestring):
            return getattr(scope[self.model_name], self.search)(operator, value)
        elif callable(self.search):
            return self.search(self, operator, value)
        else:
            return [(self.name, operator, value)]

    #
    # Setup of related fields.
    #

    @lazy_property
    def related_field(self):
        """ return the related field corresponding to `self` """
        if self.related:
            recs = scope[self.model_name]
            for name in self.related[:-1]:
                recs = recs[name]
            return recs._fields[self.related[-1]]
        return None

    def setup_related(self):
        """ Setup the attributes of the related field `self`. """
        assert self.related
        # fix the type of self.related if necessary
        if isinstance(self.related, basestring):
            self.related = tuple(self.related.split('.'))

        # check type consistency
        field = self.related_field
        if self.type != field.type:
            raise Warning("Type of related field %s is inconsistent with %s" % (self, field))

        # determine dependencies, compute, inverse, and search
        self.depends = ('.'.join(self.related),)
        self.compute = compute_related
        self.inverse = inverse_related
        self.search = search_related

        # copy attributes from field to self (readonly, required, etc.)
        field.setup()
        for attr in dir(self):
            if attr.startswith('_related_'):
                if not getattr(self, attr[9:]):
                    setattr(self, attr[9:], getattr(field, attr))

        # special case: related fields never have an inverse field!
        self.inverse_field = None

    # properties used by setup_related() to copy values from related field
    _related_string = property(attrgetter('string'))
    _related_help = property(attrgetter('help'))
    _related_readonly = property(attrgetter('readonly'))
    _related_required = property(attrgetter('required'))
    _related_states = property(attrgetter('states'))
    _related_groups = property(attrgetter('groups'))

    #
    # Field setup.
    #
    # Recomputation of computed fields: each field stores a set of triggers
    # (`field`, `path`); when the field is modified, it invalidates the cache of
    # `field` and registers the records to recompute based on `path`. See method
    # `modified` below for details.
    #

    @lazy_property
    def _triggers(self):
        """ List of pairs (`field`, `path`), where `field` is a field to
            recompute, and `path` is the dependency between `field` and `self`
            (dot-separated sequence of field names between `field.model` and
            `self.model`).
        """
        return []

    def setup(self):
        """ Complete the setup of `self`: make it process its dependencies and
            store triggers on other fields to be recomputed.
        """
        # trick: calling self.setup() again will do nothing
        self.setup = lambda: None

        model = scope[self.model_name]

        if self.related:
            # setup all attributes of related field
            self.setup_related()
        else:
            # retrieve dependencies from compute method
            if isinstance(self.compute, basestring):
                method = getattr(type(model), self.compute)
            else:
                method = self.compute

            depends = getattr(method, '_depends', ())
            self.depends = depends(model) if callable(depends) else depends

        # put invalidation/recomputation triggers on dependencies
        for path in self.depends:
            self._depends_on_model(model, [], path.split('.'))

    def _depends_on_model(self, model, path0, path1):
        """ Make `self` depend on `model`; `path0 + path1` is a dependency of
            `self`, and `path0` is the sequence of field names from `self.model`
            to `model`.
        """
        name, tail = path1[0], path1[1:]
        if name == '*':
            # special case: add triggers on all fields of model
            fields = model._fields.values()
            if not path0:
                fields.remove(self)     # self cannot depend directly on itself
        else:
            fields = (model._fields[name],)

        for field in fields:
            field._add_trigger_for(self, path0, tail)

    def _add_trigger_for(self, field, path0, path1):
        """ Add a trigger on `self` to recompute `field`; `path0` is the
            sequence of field names from `field.model` to `self.model`; ``path0
            + [self.name] + path1`` is a dependency of `field`.
        """
        self._triggers.append((field, '.'.join(path0) if path0 else 'id'))
        _logger.debug("Add trigger on field %s to recompute field %s", self, field)

    #
    # Notification when fields are modified
    #

    def modified(self, records):
        """ Notify that field `self` has been modified on `records`: prepare the
            fields/records to recompute, and return a spec indicating what to
            invalidate.
        """
        # invalidate cache for self
        spec = [(self, records._ids)]

        # invalidate the fields that depend on self, and prepare recomputation
        with scope(user=SUPERUSER_ID, context={'active_test': False}):
            for field, path in self._triggers:
                if field.store:
                    target = scope[field.model_name].search([(path, 'in', records._ids)])
                    if target:
                        spec.append((field, target._ids))
                        scope.recomputation[field] |= target
                else:
                    spec.append((field, None))

        return spec

    def modified_draft(self, records):
        """ Same as :meth:`modified`, but in draft mode. """
        spec = []

        # invalidate the fields on the records in cache that depend on `records`
        for field, path in self._triggers:
            if path == 'id':
                target = records
            else:
                target = scope[field.model_name]
                for record in target.browse(scope.cache[field]):
                    if record._map_cache(path) & records:
                        target += record
            if target:
                spec.append((field, target._ids))

        return spec

class Boolean(Field):
    """ Boolean field. """
    type = 'boolean'

    def convert_to_cache(self, value):
        return bool(value)

    def convert_to_export(self, value):
        return ustr(value)


class Integer(Field):
    """ Integer field. """
    type = 'integer'

    def convert_to_cache(self, value):
        return int(value or 0)


class Float(Field):
    """ Float field. """
    type = 'float'
    _digits = None

    _column_digits = property(lambda self: not callable(self._digits) and self._digits)
    _column_digits_compute = property(lambda self: callable(self._digits) and self._digits)
    _related_digits = property(attrgetter('digits'))
    _description_digits = property(attrgetter('digits'))

    def __init__(self, string=None, digits=None, **kwargs):
        self._digits = digits
        super(Float, self).__init__(string=string, **kwargs)

    @lazy_property
    def digits(self):
        return self._digits(scope.cr) if callable(self._digits) else self._digits

    def convert_to_cache(self, value):
        # apply rounding here, otherwise value in cache may be wrong!
        if self.digits:
            return float_round(float(value or 0.0), precision_digits=self.digits[1])
        else:
            return float(value or 0.0)


class _String(Field):
    """ Abstract class for string fields. """
    translate = False

    _column_translate = property(attrgetter('translate'))
    _related_translate = property(attrgetter('translate'))
    _description_translate = property(attrgetter('translate'))


class Char(_String):
    """ Char field. """
    type = 'char'
    size = None

    _column_size = property(attrgetter('size'))
    _related_size = property(attrgetter('size'))
    _description_size = property(attrgetter('size'))

    def convert_to_cache(self, value):
        return bool(value) and ustr(value)[:self.size]


class Text(_String):
    """ Text field. """
    type = 'text'

    def convert_to_cache(self, value):
        return bool(value) and ustr(value)


class Html(_String):
    """ Html field. """
    type = 'html'

    def convert_to_cache(self, value):
        return bool(value) and html_sanitize(value)


class Date(Field):
    """ Date field. """
    type = 'date'

    def convert_to_cache(self, value):
        if isinstance(value, (date, datetime)):
            value = value.strftime(DATE_FORMAT)
        elif value:
            # check the date format
            value = value[:DATE_LENGTH]
            datetime.strptime(value, DATE_FORMAT)
        return value or False


class Datetime(Field):
    """ Datetime field. """
    type = 'datetime'

    def convert_to_cache(self, value):
        if isinstance(value, (date, datetime)):
            value = value.strftime(DATETIME_FORMAT)
        elif value:
            # check the datetime format
            value = value[:DATETIME_LENGTH]
            if len(value) == DATE_LENGTH:
                value += " 00:00:00"
            datetime.strptime(value, DATETIME_FORMAT)
        return value or False


class Binary(Field):
    """ Binary field. """
    type = 'binary'


class Selection(Field):
    """ Selection field. """
    type = 'selection'
    selection = None        # [(value, string), ...], model method or method name

    def __init__(self, selection, string=None, **kwargs):
        """ Selection field.

            :param selection: specifies the possible values for this field.
                It is given as either a list of pairs (`value`, `string`), or a
                model method, or a method name.
        """
        if callable(selection):
            from openerp import api
            selection = api.expected(api.model, selection)
        super(Selection, self).__init__(selection=selection, string=string, **kwargs)

    def get_selection(self):
        """ return the selection list (pairs (value, label)); labels are
            translated according to context language
        """
        selection = self.selection
        if isinstance(selection, basestring):
            return getattr(scope[self.model_name], selection)()
        if callable(selection):
            return selection(scope[self.model_name])

        # translate selection labels
        if scope.lang:
            name = "%s,%s" % (self.model_name, self.name)
            translate = partial(
                scope['ir.translation']._get_source, name, 'selection', scope.lang)
            return [(value, translate(label)) for value, label in selection]
        else:
            return selection

    @property
    def _column_selection(self):
        if isinstance(self.selection, basestring):
            method = self.selection
            return lambda self, *a, **kw: getattr(self, method)(*a, **kw)
        else:
            return self.selection

    _description_selection = property(get_selection)

    def setup_related(self):
        super(Selection, self).setup_related()
        # selection must be computed on related field
        self.selection = lambda model: self.related_field.get_selection()

    def get_values(self):
        """ return a list of the possible values """
        selection = self.selection
        if isinstance(selection, basestring):
            selection = getattr(scope[self.model_name], selection)()
        elif callable(selection):
            selection = selection(scope[self.model_name])
        return [value for value, label in selection]

    def convert_to_cache(self, value):
        if value in self.get_values():
            return value
        elif not value:
            return False
        raise ValueError("Wrong value for %s: %r" % (self, value))

    def convert_to_export(self, value):
        if not isinstance(self.selection, list):
            # FIXME: this reproduces an existing buggy behavior!
            return value
        for item in self.get_selection():
            if item[0] == value:
                return item[1]
        return False


class Reference(Selection):
    """ Reference field. """
    type = 'reference'
    size = 128

    _column_size = property(attrgetter('size'))
    _related_size = property(attrgetter('size'))

    def __init__(self, selection, string=None, **kwargs):
        """ Reference field.

            :param selection: specifies the possible model names for this field.
                It is given as either a list of pairs (`value`, `string`), or a
                model method, or a method name.
        """
        super(Reference, self).__init__(selection=selection, string=string, **kwargs)

    def convert_to_cache(self, value):
        if isinstance(value, BaseModel):
            if value._name in self.get_values() and len(value) <= 1:
                return value.attach_scope(scope.current) or False
        elif isinstance(value, basestring):
            res_model, res_id = value.split(',')
            return scope[res_model].browse(int(res_id))
        elif not value:
            return False
        raise ValueError("Wrong value for %s: %r" % (self, value))

    def convert_to_read(self, value, use_name_get=True):
        return "%s,%s" % (value._name, value.id) if value else False

    def convert_to_export(self, value):
        return bool(value) and value.name_get()[0][1]

    def convert_to_display_name(self, value):
        return ustr(value and value.display_name)


class _Relational(Field):
    """ Abstract class for relational fields. """
    relational = True
    domain = None                       # domain for searching values
    context = None                      # context for searching values

    _column_obj = property(attrgetter('comodel_name'))
    _column_domain = property(attrgetter('domain'))
    _column_context = property(attrgetter('context'))

    _description_relation = property(attrgetter('comodel_name'))
    _description_domain = property(lambda self: \
        self.domain(scope[self.model_name]) if callable(self.domain) else self.domain)
    _description_context = property(attrgetter('context'))

    def __init__(self, **kwargs):
        super(_Relational, self).__init__(**kwargs)

    def null(self):
        return scope[self.comodel_name].browse()

    def _add_trigger_for(self, field, path0, path1):
        # overridden to traverse relations and manage inverse fields
        Field._add_trigger_for(self, field, path0, [])

        if self.inverse_field:
            # add trigger on inverse field, too
            Field._add_trigger_for(self.inverse_field, field, path0 + [self.name], [])

        if path1:
            # recursively traverse the dependency
            field._depends_on_model(scope[self.comodel_name], path0 + [self.name], path1)

    def modified(self, records):
        # Invalidate cache for self.inverse_field, too. Note that recomputation
        # of fields that depend on self.inverse_field is already covered by the
        # triggers (see above).
        spec = super(_Relational, self).modified(records)
        if self.inverse_field:
            spec.append((self.inverse_field, None))
        return spec


class Many2one(_Relational):
    """ Many2one field. """
    type = 'many2one'
    ondelete = 'set null'               # what to do when value is deleted
    auto_join = False                   # whether joins are generated upon search
    delegate = False                    # whether self implements delegation

    _column_ondelete = property(attrgetter('ondelete'))
    _column_auto_join = property(attrgetter('auto_join'))

    def __init__(self, comodel_name, string=None, **kwargs):
        super(Many2one, self).__init__(comodel_name=comodel_name, string=string, **kwargs)

    @lazy_property
    def inverse_field(self):
        for field in scope[self.comodel_name]._fields.itervalues():
            if isinstance(field, One2many) and field.inverse_field == self:
                return field
        return None

    @lazy_property
    def inherits(self):
        """ Whether `self` implements inheritance between model and comodel. """
        return self.name in scope[self.model_name]._inherits.itervalues()

    def _update(self, records, value):
        """ Update the cached value of `self` for `records` with `value`. """
        records._cache[self] = value

    def convert_to_cache(self, value):
        if isinstance(value, BaseModel):
            if value._name == self.comodel_name and len(value) <= 1:
                return value.attach_scope(scope.current)
            raise ValueError("Wrong value for %s: %r" % (self, value))
        elif isinstance(value, tuple):
            return scope[self.comodel_name].browse(value[0])
        elif isinstance(value, dict):
            return scope[self.comodel_name].new(value)
        else:
            return scope[self.comodel_name].browse(value)

    def convert_to_read(self, value, use_name_get=True):
        if use_name_get and value:
            # evaluate name_get() in sudo scope, because the visibility of a
            # many2one field value (id and name) depends on the current record's
            # access rights, and not the value's access rights.
            with value._scope.sudo():
                return value.name_get()[0]
        else:
            return value.id

    def convert_to_write(self, value, target=None, fnames=None):
        return bool(value) and (value.id or value._convert_to_write(value._cache))

    def convert_to_export(self, value):
        return bool(value) and value.name_get()[0][1]

    def convert_to_display_name(self, value):
        return ustr(value.display_name)

    def determine_default(self, record):
        super(Many2one, self).determine_default(record)
        if self.inherits:
            # special case: fields that implement inheritance between models
            value = record[self.name]
            if not value:
                # the default value cannot be null, use a new record instead
                record[self.name] = scope[self.comodel_name].new()


class _RelationalMulti(_Relational):
    """ Abstract class for relational fields *2many. """

    def _update(self, records, value):
        """ Update the cached value of `self` for `records` with `value`. """
        for record in records:
            record._cache[self] = record[self.name] | value

    def convert_to_cache(self, value):
        if isinstance(value, BaseModel):
            if value._name == self.comodel_name:
                return value.attach_scope(scope.current)
        elif isinstance(value, list):
            # value is a list of record ids or commands
            result = scope[self.comodel_name]
            for command in value:
                if isinstance(command, (tuple, list)):
                    if command[0] == 0:
                        result += result.new(command[2])
                    elif command[0] == 1:
                        record = result.browse(command[1])
                        record.update(command[2])
                        result += record
                    elif command[0] == 2:
                        pass
                    elif command[0] == 3:
                        pass
                    elif command[0] == 4:
                        result += result.browse(command[1])
                    elif command[0] == 5:
                        result = result.browse()
                    elif command[0] == 6:
                        result = result.browse(command[2])
                elif isinstance(command, dict):
                    result += result.new(command)
                else:
                    result += result.browse(command)
            return result
        elif not value:
            return self.null()
        raise ValueError("Wrong value for %s: %s" % (self, value))

    def convert_to_read(self, value, use_name_get=True):
        return value.unbrowse()

    def convert_to_write(self, value, target=None, fnames=None):
        result = []

        # remove/delete former records
        target = target or scope[self.model_name].browse()
        if len(target) <= 1:
            tag = 2 if self.type == 'one2many' else 3
            for record in target[self.name] - value:
                result.append((tag, record._id))
        else:
            result.append((5,))

        if fnames is None:
            # take all fields in cache, except the inverse of self
            fnames = set(scope[self.comodel_name]._fields) - set(MAGIC_COLUMNS)
            if self.inverse_field:
                fnames.discard(self.inverse_field.name)

        # add new and existing records
        for record in value:
            if not record._id or record._dirty:
                values = dict((k, v) for k, v in record._cache.iteritems() if k in fnames)
                values = record._convert_to_write(values)
                if not record._id:
                    result.append((0, 0, values))
                else:
                    result.append((1, record._id, values))
            else:
                result.append((4, record._id))

        return result

    def convert_to_export(self, value):
        return bool(value) and ','.join(name for id, name in value.name_get())

    def convert_to_display_name(self, value):
        raise NotImplementedError()


class One2many(_RelationalMulti):
    """ One2many field. """
    type = 'one2many'
    inverse_name = None                 # name of the inverse field
    auto_join = False                   # whether joins are generated upon search
    limit = None                        # optional limit to use upon read

    _column_fields_id = property(attrgetter('inverse_name'))
    _column_auto_join = property(attrgetter('auto_join'))
    _column_limit = property(attrgetter('limit'))

    _description_relation_field = property(attrgetter('inverse_name'))

    def __init__(self, comodel_name, inverse_name=None, string=None, **kwargs):
        super(One2many, self).__init__(
            comodel_name=comodel_name, inverse_name=inverse_name, string=string, **kwargs)

    @lazy_property
    def inverse_field(self):
        return self.inverse_name and scope[self.comodel_name]._fields[self.inverse_name]


class Many2many(_RelationalMulti):
    """ Many2many field. """
    type = 'many2many'
    relation = None                     # name of table
    column1 = None                      # column of table referring to model
    column2 = None                      # column of table referring to comodel
    limit = None                        # optional limit to use upon read

    _column_rel = property(attrgetter('relation'))
    _column_id1 = property(attrgetter('column1'))
    _column_id2 = property(attrgetter('column2'))
    _column_limit = property(attrgetter('limit'))

    def __init__(self, comodel_name, relation=None, column1=None, column2=None,
                string=None, **kwargs):
        super(Many2many, self).__init__(comodel_name=comodel_name, relation=relation,
            column1=column1, column2=column2, string=string, **kwargs)

    def setup(self):
        super(Many2many, self).setup()
        if self.store and not self.relation:
            model = scope[self.model_name]
            column = model._columns[self.name]
            if not isinstance(column, fields.function):
                self.relation, self.column1, self.column2 = column._sql_names(model)

    @lazy_property
    def inverse_field(self):
        if self.relation:
            expected = (self.relation, self.column2, self.column1)
            for field in scope[self.comodel_name]._fields.itervalues():
                if isinstance(field, Many2many) and \
                        (field.relation, field.column1, field.column2) == expected:
                    return field
        return None


class Id(Field):
    """ Special case for field 'id'. """
    store = False
    readonly = True

    def to_column(self):
        raise NotImplementedError()

    def __get__(self, instance, owner):
        if instance is None:
            return self         # the field is accessed through the class owner
        return instance._id

    def __set__(self, instance, value):
        raise NotImplementedError()


# imported here to avoid dependency cycle issues
from openerp import SUPERUSER_ID
from openerp.exceptions import Warning, MissingError
from openerp.osv import fields
from openerp.osv.orm import BaseModel, MAGIC_COLUMNS
from openerp.osv.scope import proxy as scope
