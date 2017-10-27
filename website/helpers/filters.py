"""Implementation of filters to be used with Ajax and URL based queries"""
import operator
import re
from collections import namedtuple
from collections import defaultdict
from collections import Iterable
from sqlalchemy import and_
from sqlalchemy import or_


def is_iterable_but_not_str(obj):
    return isinstance(obj, Iterable) and not isinstance(obj, str)


class ValidationError(Exception):
    pass


class InitializationError(Exception):
    pass


def quote_if_needed(value):
    if type(value) is not str:
        return value
    q_char = FilterManager.quote_char
    if FilterManager.sub_value_separator in value:
        if not (value.startswith(q_char) and value.endswith(q_char)):
            return q_char + value + q_char
    return value


class Filter:
    """Generic class allowing to create lists/iterators of any iterable
    objects that have compliant interface.

    Args:
        multiple:
            specify what condition should be used when testing list-based
            values. Possible values: 'any', 'all'.

            Example: when filtering a list of PTM sites by type, one might
            want to get all sites that are 'ubiquitination' and 'methylation'
            sites simultaneously: multiple='all' should be used.
            Another user would like to get sites that are either
            'ubiquitination' or 'methylation' sites: multiple='any'
            will be a good choice for that.
        type:
            type of the field used as a key when filtering models
    """

    possible_comparators = {
        'and': operator.and_,
        'or': operator.or_,
        'ge': operator.ge,
        'le': operator.le,
        'gt': operator.gt,
        'lt': operator.lt,
        'eq': operator.eq,
        'ne': operator.ne,
        'in': operator.contains,
        'ni': lambda x, y: operator.contains(y, x),
    }

    possible_join_operators = {
        'all': all,
        'any': any,
    }

    def _check_comparators(self, comparators):
        for comparator in comparators:
            if comparator not in self.possible_comparators.keys():
                raise InitializationError('Unknown comparator %s' % comparator)

    def __init__(
        self, targets, attribute, default=None, nullable=True,
        comparators='__all__', choices=None,
        default_comparator=None, multiple=False,
        is_attribute_a_method=False, as_sqlalchemy=False, type=None,
        skip_if_default=False
    ):
        if comparators == '__all__':
            comparators = self.possible_comparators.keys()

        self.skip_if_default = skip_if_default
        self._check_comparators(comparators)

        if not default_comparator and len(comparators) == 1:
            default_comparator = comparators[0]

        self.allowed_comparators = comparators
        self.choices = choices
        self.targets = (
            targets
            if is_iterable_but_not_str(targets)
            else [targets]
        )
        self.is_attribute_a_method = is_attribute_a_method
        self.default = default
        self.attribute = attribute
        self.multiple = multiple
        self.nullable = nullable
        self._value = None
        self.manager = None
        if default_comparator:
            self._verify_comparator(default_comparator)
        self._default_comparator = default_comparator
        self._comparator = None
        self._as_sqlalchemy = as_sqlalchemy
        self.type = type

        if default and not default_comparator:
            raise InitializationError(
                'When specifying default value, the default comparator '
                'is also required'
            )

    @property
    def has_sqlalchemy(self):
        return self._as_sqlalchemy

    def as_sqlalchemy(self, target):
        from sqlalchemy.ext.associationproxy import AssociationProxy
        from sqlalchemy.sql.annotation import AnnotatedSelect
        from sqlalchemy.sql.sqltypes import Text
        from types import FunctionType

        comparators = {
            'ge': '__ge__',
            'le': '__le__',
            'gt': '__gt__',
            'lt': '__lt__',
            'eq': '__eq__',
            'in': 'in_',
            'ni': 'notin_'
        }

        join_operators = {
            'all': and_,
            'any': or_
        }

        if self.value is None:
            return None, []

        if type(self._as_sqlalchemy) is FunctionType:
            return self._as_sqlalchemy(self, target), []

        path = self.attribute.split('.')

        assert len(path) < 3     # we are unable to query deeper easily

        field = getattr(target, path[0])

        # Possible upgrade:
        #   from sqlalchemy.orm.attributes import QueryableAttribute
        #   if isinstance(field, QueryableAttribute):
        if type(field) is AnnotatedSelect:
            if self.comparator == 'eq':
                return field, []

        if type(field) is AssociationProxy:
            # additional joins may be needed when using proxies

            joins = []

            while type(field) is AssociationProxy:
                joins.append(field.target_class)
                field = field.remote_attr

            if self.comparator == 'in':

                if self.multiple == 'any':
                    # this wont give expected result for 'all'
                    func = getattr(field, comparators[self.comparator])
                    return func(self.value), joins
                else:
                    # this works for 'any' too (but it's uglier)
                    func = getattr(field, '__eq__')

                    comp_func = join_operators[self.multiple](
                        *[
                            func(sub_value)
                            for sub_value in self.value
                        ]
                    )
                    return comp_func, joins

        if len(path) == 2:
            if self.comparator == 'in':
                sub_attr = path[1]
                func = getattr(field, 'any')

                values = self.value if is_iterable_but_not_str(self.value) else [self.value]
                comp_func = join_operators[self.multiple](
                    *[
                        func(**{sub_attr: sub_value})
                        for sub_value in values
                    ]
                )
                return comp_func, []

        if (
            self.comparator == 'in' and
            type(field.property.columns[0].type) is Text
        ):
            return getattr(field, 'like')('%' + self.value + '%'), []

        return getattr(field, comparators[self.comparator])(self.value), []

    @property
    def primary_target(self):
        return self.targets[0]

    @property
    def id(self):
        return self.primary_target.__name__ + '.' + self.attribute

    def _verify_value(self, value, raise_on_forbidden=True):
        if not (
                self.nullable or
                value
        ):
            raise ValidationError(
                'Filter %s is not nullable' % self.id
            )
        elif self.choices and not (
                not value or
                (
                    is_iterable_but_not_str(value) and
                    all(
                        sub_value in self.choices
                        for sub_value in value
                    )
                ) or
                (
                    not is_iterable_but_not_str(value) and
                    value in self.choices
                )
        ):
            if raise_on_forbidden:
                raise ValidationError(
                    'Filter %s received forbidden value: %s. Allowed: %s. '
                    'Check types.' % (self.id, value, self.choices)
                )
            else:
                return self.choices

    def _verify_comparator(self, comparator):
        if comparator not in self.allowed_comparators:
            raise ValidationError(
                'Filter %s received forbidden comparator: %s. Allowed: %s' %
                (self.id, comparator, self.allowed_comparators)
            )

    def _verify(self, value, comparator):
        self._verify_value(value)
        self._verify_comparator(comparator)

    def update(self, value, comparator=None, raise_on_forbidden=True):
        """Update filter with given value and (optionally) comparator.

        If given value (or part of it) is not allowed on the filter
        it will be either returned as rejected or a ValidationError
        will be raise - depending on raise_on_forbidden value.
        """
        rejected = set()
        if comparator:
            self._verify_comparator(comparator)
            self._comparator = comparator

        if self.multiple and not is_iterable_but_not_str(value):
            value = [value]
        # cast to desired type
        if self.type:
            if self.multiple:
                value = [self.type(sub_value) for sub_value in value]
            else:
                value = self.type(value)
        accepted_values = self._verify_value(value, raise_on_forbidden)
        if not raise_on_forbidden and accepted_values:
            if self.multiple:
                retained = set(value).intersection(accepted_values)
                rejected = set(value) - retained
                value = list(retained)
            # raise if we cannot fix it
            self._verify_value(value, True)
        self._value = value

        return rejected

    def get_multiple_function(self):
        if self.multiple and is_iterable_but_not_str(self.value):
            return self.possible_join_operators[self.multiple]

    def compare(self, value):

        comparator_function = self.possible_comparators[self.comparator]
        multiple_test = self.get_multiple_function()

        compare = self.get_compare_func(comparator_function, multiple_test)

        return compare(value)

    def get_compare_func(self, comparator_function, multiple_test):
        if multiple_test:
            def compare(value):
                # tricky: check if operator is usable on given value.
                # Detects when one tries to check if x in None or y > "tree".
                # As all of those are incorrect false will be returned.
                try:
                    comparator_function(value, value)
                except TypeError:
                    return lambda x: False

                return multiple_test(
                    comparator_function(value, sub_value)
                    for sub_value in self.value
                )
            return compare

        def compare(value):

            try:
                comparator_function(value, value)
            except TypeError:
                return lambda x: False
            return comparator_function(value, self.value)

        return compare

    def attr_getter(self):
        """Attrgetter that passes a value to an method-attribute if needed"""

        # handle custom arguments getters
        if hasattr(self.primary_target, self.attribute):
            field = getattr(self.primary_target, self.attribute)
            if hasattr(field, 'custom_attr_getter'):
                return field.custom_attr_getter

        getter = operator.attrgetter(self.attribute)
        if self.is_attribute_a_method:
            def attr_get(element):
                return getter(element)(self.manager)
        else:
            attr_get = getter
        return attr_get

    def test(self, obj):
        """Test if a given object (instance) passes criteria of this filter.

        If a property does not exists in tested object -1 will be returned,
        to indicate that the filter is not applicable to the passed object.

        Example: filter(my_filter.test, list_of_model_objects)
        Note that an object without tested property will remain on the list.
        """
        if self.value is None:
            # the filter is turned off
            return -1

        attr_get = self.attr_getter()
        obj_value = attr_get(obj)
        return self.compare(obj_value)

    def apply(self, elements, itemgetter=None):
        """Optimized equivalent to list(filter(my_filter.test, elements))"""

        if self.value is None:
            # the filter is turned off
            return -1

        if not elements:
            return []

        attr_get = self.attr_getter()
        if itemgetter:
            old_attr_get = attr_get

            def attr_get(element):
                element = itemgetter(element)
                return old_attr_get(element)

        comparator_function = self.possible_comparators[self.comparator]
        multiple_test = self.get_multiple_function()

        compare = self.get_compare_func(comparator_function, multiple_test)

        return (
            elem
            for elem in elements
            if compare(
                attr_get(elem)
            )
        )

    @property
    def is_active(self):    # TODO rename to 'is_applicable' or so
        return self.visible and (
            self.value is not None or self.value != self.default
        )

    @property
    def value(self):
        if self._value is not None:
            return self._value
        return self.default

    @property
    def comparator(self):
        if self._comparator:
            return self._comparator
        return self._default_comparator

    visible = True      # TODO rename to 'active'

    def __repr__(self):
        return '<Filter {1} ({0}active) with value "{2}">'.format(
            '' if self.is_active else 'in',
            self.id,
            self.value
        )


def split_with_quotation(value):
    from csv import reader

    for v in reader(
        [value],
        delimiter=FilterManager.sub_value_separator,
        quotechar=FilterManager.quote_char
    ):
        return v


def unqoute(value):
    q_char = FilterManager.quote_char
    if value.startswith(q_char) and value.endswith(q_char):
        return value[1:-1]
    return value


def joined_query(query, required_joins):
    already_joined = set()
    for joins in required_joins:
        for join in joins:
            if join not in already_joined:
                query = query.join(join)
                already_joined.add(join)
    return query


class FilterManager:
    """Main class used to parse & apply filters' data specified by request.

    All allowed filters have to be registered during initialization."""

    # An example of separators use:
    # mutation.frequency:gt:0.2;mutation.cancer:in:KIRC,COAD
    filters_separator = ';'
    field_separator = ':'
    sub_value_separator = ','
    quote_char = '\''

    # a shorthand for structure to update filters
    UpdateTuple = namedtuple('UpdateTuple', ['id', 'comparator', 'value'])

    def __init__(self, filters):
        # let each filter know where to seek data about other filters
        # (so one filter can relay on state of other filters,
        # eg. be active conditionally, if another filter is set).

        for filter in filters:
            filter.manager = self

        # store filters as dict of filter_id -> filter for quick access
        self.filters = {
            filter.id: filter
            for filter in filters
        }

    def get_active(self):
        """Return a list of active filters"""
        return [f for f in self.filters.values() if f.is_active]

    def get_inactive(self):
        """Return a list of inactive filters"""
        return [f for f in self.filters.values() if not f.is_active]

    def prepare_filters(self, target=None):

        to_apply_manually = []
        query_filters = []
        all_required_joins = []

        for the_filter in self._get_non_trivial_active(target):

            if the_filter.has_sqlalchemy:

                the_target = target if target else the_filter.targets[0]

                as_sqlalchemy, required_joins = the_filter.as_sqlalchemy(the_target)
                if as_sqlalchemy is not None:
                    query_filters.append(as_sqlalchemy)
                    all_required_joins.append(required_joins)

            else:
                to_apply_manually.append(the_filter)

        return query_filters, to_apply_manually, all_required_joins

    def build_query(self, target, custom_filter=None, query_modifier=None):
        """There are two strategies of using filter manager:

            - you can get results from database and walk through
              every element in the list, or
            - you can build a query and move some job to the database;
              not always it is possible though.
        """
        query_filters, to_apply_manually, required_joins = self.prepare_filters(target)

        query_filters_sum = and_(*query_filters)

        if custom_filter:
            query_filters_sum = custom_filter(query_filters_sum)

        query = joined_query(target.query, required_joins)
        query = query.filter(query_filters_sum)

        if query_modifier:
            query = query_modifier(query)

        return query, to_apply_manually

    def query_all(self, target, custom_filter=None, query_modifier=None):
        """Retrieve all objects of type 'target' which
        match criteria of currently active filters.
        """

        query, to_apply_manually = self.build_query(target, custom_filter, query_modifier)

        return self.apply(query, to_apply_manually)

    def query_count(self, target, custom_filter=None, query_modifier=None):
        """Retrieve count of all objects of type 'target' which
        match criteria of currently active filters.
        """

        query, to_apply_manually = self.build_query(target, custom_filter, query_modifier)

        if not to_apply_manually:
            return query.with_entities(target.id).count()
        else:
            return len(self.apply(query, to_apply_manually))

    def apply(self, elements, filters_subset=None, itemgetter=None):
        """Apply all appropriate filters to given list of elements.

        Only filters targeting the same model and being currently active will
        be applied. The target model will be deduced from passed elements.
        """
        try:
            tester = elements[0]
            if itemgetter:
                tester = itemgetter(tester)

            target_type = type(tester)
        except IndexError:
            # the list was empty or was emptied before we were able to
            # investigate the type of targeted objects
            return []

        if filters_subset is not None:
            filters = filters_subset
        else:
            filters = self._get_non_trivial_active(target_type)

        for filter_ in filters:
            elements = filter_.apply(elements, itemgetter)

        return list(elements)

    def _get_non_trivial_active(self, target=None):
        non_trivial_filters = []
        for filter_ in self._get_active(target):
            if filter_.skip_if_default and set(filter_.default) == set(filter_.value):
                continue
            non_trivial_filters.append(filter_)
        return non_trivial_filters

    def _get_active(self, target=None):
        """Return filters which are active & target the same type of objects"""
        return [
            the_filter
            for the_filter in self.filters.values()
            if (
                (not target or target in the_filter.targets) and
                the_filter.is_active
            )
        ]

    def get_value(self, filter_id):
        """Return value of filter with specified identifier."""
        return self.filters[filter_id].value

    def update_from_request(self, request, raise_on_forbidden=True):
        """Set states of child filters to match those specified in request.

        The query part of request will be looked upon to get filter's data, in
        one of two available formats: modern or fallback.

        For details see _parse_request() method in this class.

        Returns:
            tuple: skipped, rejected

            skipped: updates skipped, from unrecognized filters
            rejected: updates skipped, from forbidden values (if raise_on_forbidden was False)
        """
        filter_updates = self._parse_request(request)

        skipped = []
        rejected = defaultdict(list)

        for update in filter_updates:
            if update.id not in self.filters:
                skipped.append(update)
                continue

            rejected_updates = self.filters[update.id].update(
                self._parse_value(update.value),
                self._parse_comparator(update.comparator),
                raise_on_forbidden=raise_on_forbidden
            )

            if rejected_updates:
                rejected[update.id].extend(rejected_updates)

        return skipped, rejected

    def _parse_fallback_query(self, args):
        """Parse query in fallback format."""

        re_value = re.compile(r'filter\[([\w.]+)\]')
        re_cmp = re.compile(r'filter\[([\w.]+)\]\[cmp\]')

        filters = defaultdict(lambda: defaultdict(list))

        for key, value in args.items():

            match = re_value.fullmatch(key)
            if match:
                name = match.group(1)
                filters[name]['value'] = value

            match = re_cmp.fullmatch(key)
            if match:
                name = match.group(1)
                filters[name]['cmp'] = value

        filters_list = [
            [
                filter_name,
                data.get('cmp', None),    # allow not specifying comparator -
                # if so, we will use default comparator.
                self._repr_value(data.get('value'))
            ]
            for filter_name, data in filters.items()
        ]
        return filters_list

    def _parse_request(self, request):
        """Extract and normalize filters' data from Flask's request object.

        Arguments will be extracted from request.args or request.form.

        If arguments contain 'clear_filters' command,
        all other arguments will be ignored.

        Two formats for specifying filters are available:
            modern request format:
                Model.filters=is_ptm:eq:True;Model.frequency:gt:2
            fallback format:
                filter[Model.is_ptm]=True&filter[Model.frequency]=2&\
                filter[Model.frequency][cmp]=gt&fallback=True

        where fallback format is what will be generated by browsers after
        posting HTML form and modern format is designed to be used with AJAX
        requests, to create readable REST interfaces and for internal usage.
        """
        if request.method == 'GET':
            args = request.args
        else:
            args = request.form

        if args.get('clear_filters'):
            filters_list = self._parse_fallback_query({})
        elif args.get('fallback'):
            filters_list = self._parse_fallback_query(dict(args))
        else:
            filters_list = self._parse_string(
                args.get('filters', '')
            )

        return [
            self.UpdateTuple(*filter_update)
            for filter_update in filters_list
        ]

    def _parse_string(self, filters_string):
        """Split modern query string into list describing filter's settings."""
        raw_filters = filters_string.split(self.filters_separator)

        # remove empty strings
        raw_filters = filter(bool, raw_filters)

        filters_list = [
            filter_update.split(self.field_separator, maxsplit=2)
            for filter_update in raw_filters
        ]
        return filters_list

    @staticmethod
    def _parse_comparator(comparator):
        if comparator == 'None':
            return None
        return comparator

    def _parse_value(self, value, do_not_split=False):
        """Safely parse value from string, without eval.

        For sub-values quotations will be respected.
        """

        if value == 'True':
            return True
        elif value == 'False':
            return False
        elif value == 'None':
            return None
        elif value.isdigit():
            return int(value)

        value = value.replace('+', ' ')

        splitted = split_with_quotation(value)

        if not do_not_split and len(splitted) > 1:
            return [self._parse_value(v, True) for v in splitted]

        value = unqoute(value)

        return value

    def _repr_value(self, value):
        """Return string representation of given value (of a filter)."""
        if is_iterable_but_not_str(value):
            return self.sub_value_separator.join([
                quote_if_needed(str(v))
                for v in value
            ])

        return str(value)

    def url_string(self, expanded=False):
        """String representation of filters from the set for use in URL address.

        Produced string is ready to be included as a query argument in Flask's
        `url_for` func. If no string has been produced, None will be returned.

        Args:
            expanded:
                should all active filters be included
                (also those with value set to default)
        """
        return self.filters_separator.join(
            [
                self.field_separator.join(
                    map(str, [
                        f.id,
                        f.comparator,
                        self._repr_value(f.value)
                    ])
                )
                for f in self.filters.values()
                if f.is_active and (
                    f.value != f.default
                    or expanded
                )
            ]
        ) or None   # if empty string has been built, return None to indicate
        # that there is really nothing interesting (keeps address clean when
        # result is being passed as a keyword arg to flask's url_for function)

    def reset(self):
        """Reset values of child filters to bring them into a neutral state."""
        for filter in self.filters.values():
            filter._value = None

    def reformat_request_url(self, request, endpoint, *args, **kwargs):
        from flask import url_for
        from flask import redirect
        from flask import current_app

        if request.args.get('fallback'):

            scheme = current_app.config.get('PREFERRED_URL_SCHEME', 'http')

            url = url_for(endpoint, *args,  _external=True, _scheme=scheme, **kwargs)

            filters = self.url_string()

            args = dict(request.args)
            # filters might be empty if
            # (e.g. if all are pointing to default values)
            if filters:
                args['filters'] = [filters]

            query_string = '&'.join(
                [
                    arg + '=' + value[0]
                    for arg, value in args.items()
                    if not (arg.startswith('filter[') or arg == 'fallback')
                ]
            )
            # add other arguments
            if query_string:
                url += '?' + query_string

            return redirect(url)
