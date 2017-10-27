/**
 * jQuery object
 * @external jQuery
 * @see {@link http://api.jquery.com/jQuery/}
 */

/**
 * Server response for filtering query for given representation.
 * @typedef {Object} ServerResponse
 * @property {FiltersData} filters
 * @property content - Content to be passed to provided data_handler
 */

/**
 * Filter-specific metadata allowing to recognise if a response id up-to-date,
 * to update widgets if dataset has changed and so on.
 * @typedef {Object} FiltersData
 * @property {boolean} checksum - Semaphore-like checksum of filters handled
 * @property {html} dynamic_widgets - Widgets applicable only to selected dataset
 * @property {string} query - Value of query as to be used in 'filters={{value}}' URL query string.
 * @property {string} expanded_query - Like query but including default filters values.
 */


/**
 * @class
 * @return {{init: init, value: get_value, load: load, apply: apply, on_update: on_update}}
 */
var AsyncFiltersHandler = function()
{
    var config;
    var form;
    var current_state_checksum;

    /**
     * Generate checksum string for given query string.
     * @param {string} query
     * @returns {string} checksum of given query string
     */
    function make_checksum(query)
    {
        return md5(query);
    }

    /**
     * Serialize a form into a GET-parameters list (for use in URLs),
     * additionally, a checksum parameter (md5) will be added.
     * @param {jQuery} $form - Form to be serialized.
     * @returns {string} Generated query string (parameters).
     */
    function serialize_form($form)
    {
        var filters_query = $form.serialize();
        var checksum = make_checksum(filters_query);

        filters_query += filters_query ? '&' : '?';

        filters_query += 'checksum=' + checksum;
        return filters_query
    }

    /**
     * Transform current URL (location.href) to include provided parameters.
     * @param {string} parameters - Query string to be included.
     * @returns {string} Current URL updated with provided filters query.
     */
    function make_query_url(parameters)
    {
        var location = window.location.href;
        var splitted = location.split('#');
        var hash = splitted.length > 0 ? splitted[1] : '';
        location = splitted[0].split('?')[0];

        if (parameters) {
            location += '?' + parameters;
        }

        if (hash) {
            location += '#' + hash;
        }
        return location;
    }

    /** Callback to update event which will be bound to the form. */
    function on_update(do_not_save)
    {
        // do not apply filters till all widget blockades are released
        if(form.find('.block').length !== 0) {
            return false
        }

        var filters_query = serialize_form(form);

        apply(filters_query, do_not_save);
    }

    /**
     * Serialize as much as (easily) possible inside given DOM structure.
     * Generally used to compare two HTML fragments containing similar but not identical forms.
     * If for both fragments the function returns the same, then then values of inputs are identical.
     * @param {jQuery} $dom_fragment - Part of HTML document to serialize.
     * @returns {string} Query string from serialized inputs inside given DOM fragment.
     */
    function serialize_fragment($dom_fragment)
    {
        return $dom_fragment.find('input,select').serialize()
    }

    /**
     * @param {FiltersData} data
     * @returns {boolean} is response up to date?
     */
    function is_response_actual(data)
    {
        return make_checksum(form.serialize()) === data.checksum;
    }

    /**
     * Prevents concurrency issue of accepting two or more consecutive
     * responses, which all pass "is_response_actual" test.
     * It prevents effect of duplicated visualisations as shown in #121 issue.
     * @param {FiltersData} data
     * @returns {boolean} is different with currently set value?
     */
    function does_response_differ_from_current_state(data)
    {
        return current_state_checksum != data.checksum
    }

    /**
     * Replace filters form with relevant (updated) content:
     * - set up dynamic widgets (widgets which change, depending on
     *   values of other filters, e.g. dataset-specific widgets: we
     *   do not want to filter by cancer type in ESP6500 dataset)
     * - correctly selected checkboxes / inputs
     *   (when restoring to the old state with History API,
     *   those has to be replaced accordingly to old state)
     * @param {FiltersData} data - server response defining current state
     * @param {boolean} from_future - Was the update called on "popstate" History API event?
     */
    function update_form_html(data, from_future)
    {
        var html = $.parseHTML(data.dynamic_widgets);

        if(from_future)
        {
            form.get(0).reset()
            form.deserialize(history.state.form)
            form.trigger('PotentialAffixChange');
        }

        var dynamic_widgets = $('.dynamic-widgets');

        // do not replace if it's not needed - so expanded lists stay expanded
        if(serialize_fragment(dynamic_widgets) !== serialize_fragment($(html)))
        {
            dynamic_widgets.html(html);
            dynamic_widgets.trigger('PotentialAffixChange');
        }
    }

    /**
     * Update an HTML <a> link, substituting '{{ filters }}' with given
     * filters string and cleaning up resultant URL from unused parameters.
     * @param {HTMLElement} element - <a> element to be updated; has to contain appropriate 'data-url-pattern'
     * @param {string} filters_string - query string to be used
     */
    function update_link(element, filters_string)
    {
        var $a = $(element);

        var pattern = decode_url_pattern($a.data('url-pattern'));
        var new_href = format(pattern, {filters: filters_string});

        // clean up the url from unused parameters
        new_href = new_href.replace(/(&|\?)(.*?)=(&|$)/, '');

        $a.attr('href', new_href);
    }

    /**
     * Handle response from.
     * @param {ServerResponse} data - server response defining current state
     * @param {boolean} from_future - Was the update called on "popstate" History API event?
     */
    function load(data, from_future)
    {
        var filters_data = data.filters;

        if (!(is_response_actual(filters_data) && does_response_differ_from_current_state(filters_data)) && !from_future)
        {
            console.log('Skipping outdated response');
            return
        }
        current_state_checksum = filters_data.checksum

        config.data_handler(data.content, filters_data);

        update_form_html(filters_data, from_future);

        if(config.links_to_update)
        {
            config.links_to_update.each(function(){
                update_link(this, filters_data.query)
            });
        }

        config.on_loading_end();
    }

    function update_history(query, replace)
    {
        var history_action = history.pushState;

        if (replace)
            history_action = history.replaceState;

        var state = {filters_query: query, form: form.serialize(), handler: 'filters'};

        history_action(state, '', make_query_url(query));
    }

    /**
     * Apply filters provided in query:
     *  - ask server for data for those filters,
     *  - change URL,
     *  - record changes with History API.
     * @param {string} filters_query - Query string as returned by {@see serialize_form}
     * @param {boolean} [do_not_save=false] - Should this modification be recorded in history?
     * @param {boolean} [from_future=false] - Was called on "popstate" History API event?
     */
    function apply(filters_query, do_not_save, from_future)
    {
        config.on_loading_start();
        current_state_checksum = null;

        $.ajax({
            url: config.endpoint_url,
            data: filters_query,
            success: function(data){

                var filters_query = ''

                if(data.filters.query)
                    filters_query += 'filters=' + data.filters.query

                load(data, from_future)

                update_history(filters_query, do_not_save)

            }
        });
    }

    /**
     * Retrieve currently set value of a filter of given name.
     *
     * Returns undefined if there is no selected value or if
     * the current value is default one and not explicitly set.
     */
    function get_value(filter_name)
    {
        var matched = form.serializeArray().filter(
            function(o){ return o.name === 'filter[' + filter_name + ']' }
        )
        if(matched.length !== 1)
            return undefined
        return matched[0].value
    }

    /**
     * Configuration object for AsyncFiltersHandler.
     * @typedef {Object} Config
     * @memberOf AsyncFiltersHandler
     * @property {jQuery} form
     * @property {function} data_handler
     * @property {function} on_loading_start
     * @property {function} on_loading_end
     * @property {number} input_delay
     * @property {jQuery} links_to_update
     * @property {string} endpoint_url - an URL of endpoint returning {@see ServerResponse}
     *  the endpoint should accept checksum, (and return in {@see FiltersData})
     */

    return {
        /**
         * Initialize AsyncFiltersHandler
         * @memberOf AsyncFiltersHandler
         * @param {Config} new_config
         */
        init: function(new_config)
        {
            config = new_config;
            form = config.form;
            form.on(
                'change',
                'select, input:not([type=text]):not(.programmatic)',
                function() { on_update() }
            );

            var timer;

            form.on(
                'input',
                'input[type=text]:not(.programmatic)',
                function() {

                    if(timer)
                        window.clearTimeout(timer)

                    timer = window.setTimeout(
                        function(){
                            timer = null
                            on_update()
                        },
                        config.input_delay || 200
                    )
                }
            );

            form.find('.save').hide()

            update_history(window.location.search.substring(1), true)
        },
        value: get_value,
        load: load,
        apply: apply,
        on_update: on_update
    };
};
