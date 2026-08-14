(function () {
    'use strict';

    const config = window.FRPDepotDerakaneConfig || {};
    const minimumCharacters = Number(config.minChars || 2);
    const debounceMilliseconds = Number(config.debounceMs || 250);

    function element(tagName, className, text) {
        const node = document.createElement(tagName);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function alternativeText(alternative) {
        if (alternative.kind === 'temperature_pair') {
            return alternative.components.map((component) =>
                `${component.limited_service ? 'LS ' : ''}${component.value}°${component.unit}`
            ).join(' / ');
        }
        if (alternative.kind === 'single_temperature') {
            return `${alternative.value} (unit not printed)`;
        }
        if (alternative.kind === 'printed_dash') return '-';
        return '';
    }

    function sourceSpecialText(rating) {
        if (rating.source_special_form === '510_variant_specific_values' && rating.resin_variants) {
            return rating.resin_variants.assignments.map((assignment) => {
                const label = assignment.label_raw.replace(/\s+/g, ' ').trim();
                return `${label} ${alternativeText(assignment.rating)}`;
            }).join('; ');
        }
        return rating.alternatives.map(alternativeText).filter(Boolean).join('; ');
    }

    function ratingText(rating) {
        if (rating.state === 'value') return `${rating.temperature_c}°C / ${rating.temperature_f}°F`;
        if (rating.state === 'source_special') return sourceSpecialText(rating);
        if (rating.state === 'nr') return 'NR';
        if (rating.state === 'ls') return 'LS';
        return '';
    }

    function cellText(cell) {
        return cell.ratings.map(ratingText).filter(Boolean).join('; ');
    }

    function renderGroup(group, resinColumns) {
        const article = element('article', 'derakane-result');
        const title = element('h2', 'derakane-result__title', group.chemical_name);
        article.appendChild(title);

        const metadataParts = [];
        const aliases = (group.aliases || []).map((alias) => alias.display);
        const publicCasNumbers = group.public_cas_numbers || [];
        if (aliases.length) metadataParts.push(`Aliases: ${aliases.join(', ')}`);
        if (publicCasNumbers.length) metadataParts.push(`CAS: ${publicCasNumbers.join(', ')}`);
        if (metadataParts.length) {
            article.appendChild(element('p', 'derakane-result__metadata', metadataParts.join(' · ')));
        }

        const cue = element('p', 'derakane-table__mobile-cue', 'On a small screen, swipe horizontally to view all resin columns.');
        cue.setAttribute('aria-hidden', 'true');
        article.appendChild(cue);

        const wrapper = element('div', 'derakane-table-wrap');
        wrapper.setAttribute('tabindex', '0');
        wrapper.setAttribute('role', 'region');
        wrapper.setAttribute('aria-label', `Scrollable resistance table for ${group.chemical_name}`);
        const table = element('table', 'derakane-table');
        table.appendChild(element('caption', '', `Maximum guide service temperature by resin series for ${group.chemical_name}. Values are °C/°F.`));
        const head = document.createElement('thead');
        const headRow = document.createElement('tr');
        const concentrationHeading = element('th', '', 'Concentration');
        concentrationHeading.scope = 'col';
        headRow.appendChild(concentrationHeading);
        resinColumns.forEach((column) => {
            const heading = element('th', '', column.label);
            heading.scope = 'col';
            headRow.appendChild(heading);
        });
        const sourceHeading = element('th', '', 'Source');
        sourceHeading.scope = 'col';
        headRow.appendChild(sourceHeading);
        head.appendChild(headRow);
        table.appendChild(head);

        const body = document.createElement('tbody');
        group.rows.forEach((row) => {
            const tr = document.createElement('tr');
            const concentration = element('th', '', row.concentration.display);
            concentration.scope = 'row';
            tr.appendChild(concentration);
            row.cells.forEach((cell) => {
                const td = element('td', '', cellText(cell));
                if (cell.ratings.every((rating) => rating.state === 'blank')) {
                    td.setAttribute('aria-label', 'Blank: no data was available when the guide ratings were assigned.');
                }
                tr.appendChild(td);
            });
            tr.appendChild(element('td', 'derakane-table__source', `Page ${row.source_page}; row ${row.source_sequence}`));
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrapper.appendChild(table);
        article.appendChild(wrapper);

        if (group.footnotes.length) {
            const footnotes = element('section', 'derakane-result__footnotes');
            const footnoteHeading = element('h3', '', 'Essential footnotes for this result');
            footnotes.appendChild(footnoteHeading);
            const list = document.createElement('ol');
            group.footnotes.forEach((footnote) => {
                const item = element('li', '', footnote.text);
                item.value = footnote.id;
                item.appendChild(element('span', 'derakane-result__footnote-source', ` (source page ${footnote.source_page})`));
                list.appendChild(item);
            });
            footnotes.appendChild(list);
            article.appendChild(footnotes);
        }
        return article;
    }

    function initSearch(root) {
        const form = root.querySelector('[data-derakane-search-form]');
        const input = root.querySelector('[data-derakane-search-input]');
        const concentration = root.querySelector('[data-derakane-concentration]');
        const resin = root.querySelector('[data-derakane-resin]');
        const status = root.querySelector('[data-derakane-search-status]');
        const results = root.querySelector('[data-derakane-search-results]');
        const loadMore = root.querySelector('[data-derakane-load-more]');
        if (!form || !input || !concentration || !resin || !status || !results || !loadMore || !config.restUrl) return;

        let timer = null;
        let controller = null;
        let requestSequence = 0;
        let nextOffset = null;
        let currentTotal = 0;
        let renderedCount = 0;

        function setStatus(message) {
            status.textContent = message || '';
        }

        function updateHistory(mode) {
            if (mode === 'none') return;
            const url = new URL(window.location.href);
            const chemical = input.value.trim();
            if (chemical) url.searchParams.set('chemical', chemical);
            else url.searchParams.delete('chemical');
            if (concentration.value) url.searchParams.set('concentration', concentration.value);
            else url.searchParams.delete('concentration');
            if (resin.value) url.searchParams.set('resin', resin.value);
            else url.searchParams.delete('resin');
            const method = mode === 'push' ? 'pushState' : 'replaceState';
            window.history[method]({ chemical, concentration: concentration.value, resin: resin.value }, '', url.toString());
        }

        function resetConcentrationOptions(options, selected) {
            concentration.replaceChildren();
            const all = element('option', '', 'All listed concentrations');
            all.value = '';
            concentration.appendChild(all);
            options.forEach((value) => {
                const option = element('option', '', value);
                option.value = value;
                concentration.appendChild(option);
            });
            concentration.value = options.includes(selected) ? selected : '';
            concentration.disabled = options.length === 0;
        }

        function renderPayload(payload, append) {
            if (!append) results.replaceChildren();
            payload.groups.forEach((group) => results.appendChild(renderGroup(group, payload.resin_columns)));
            nextOffset = payload.next_offset;
            currentTotal = payload.total;
            renderedCount = append ? renderedCount + payload.groups.length : payload.groups.length;
            loadMore.hidden = nextOffset === null;
            loadMore.disabled = false;
            loadMore.textContent = 'Load more';
            setStatus(currentTotal === 0
                ? 'No exact guide entries matched your search and filters.'
                : `Showing ${renderedCount} of ${currentTotal} matching chemical ${currentTotal === 1 ? 'result' : 'results'}.`);
        }

        async function runSearch(options) {
            const settings = Object.assign({ append: false, history: 'replace' }, options || {});
            const chemical = input.value.trim();
            if (chemical.length < minimumCharacters) {
                if (controller) controller.abort();
                requestSequence += 1;
                results.replaceChildren();
                resetConcentrationOptions([], '');
                loadMore.hidden = true;
                nextOffset = null;
                renderedCount = 0;
                setStatus(`Enter at least ${minimumCharacters} characters.`);
                updateHistory(settings.history);
                return;
            }

            if (controller) controller.abort();
            controller = new AbortController();
            const thisController = controller;
            const thisSequence = ++requestSequence;
            const selectedConcentration = settings.concentration === undefined
                ? concentration.value
                : settings.concentration;
            const parameters = new URLSearchParams({ chemical });
            if (selectedConcentration) parameters.set('concentration', selectedConcentration);
            if (resin.value) parameters.set('resin', resin.value);
            if (settings.append && nextOffset !== null) parameters.set('offset', String(nextOffset));

            if (settings.append) {
                loadMore.disabled = true;
                loadMore.textContent = 'Loading…';
            } else {
                setStatus('Searching verified guide entries…');
            }
            updateHistory(settings.history);

            try {
                const response = await fetch(`${config.restUrl}?${parameters.toString()}`, {
                    method: 'GET',
                    credentials: 'same-origin',
                    headers: { Accept: 'application/json' },
                    signal: thisController.signal
                });
                if (!response.ok) throw new Error(`Search failed with HTTP ${response.status}`);
                const payload = await response.json();
                if (thisSequence !== requestSequence) return;
                if (!settings.append) resetConcentrationOptions(payload.concentration_options || [], selectedConcentration);
                renderPayload(payload, settings.append);
            } catch (error) {
                if (error && error.name === 'AbortError') return;
                if (thisSequence !== requestSequence) return;
                if (!settings.append) results.replaceChildren();
                loadMore.hidden = true;
                loadMore.disabled = false;
                loadMore.textContent = 'Load more';
                setStatus('The verified guide search could not be completed. Please try again.');
            }
        }

        form.addEventListener('submit', (event) => {
            event.preventDefault();
            window.clearTimeout(timer);
            runSearch({ append: false, history: 'push' });
        });
        input.addEventListener('input', () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => runSearch({ append: false, history: 'replace' }), debounceMilliseconds);
        });
        concentration.addEventListener('change', () => runSearch({ append: false, history: 'push' }));
        resin.addEventListener('change', () => runSearch({ append: false, history: 'push' }));
        loadMore.addEventListener('click', () => {
            if (nextOffset !== null) runSearch({ append: true, history: 'none' });
        });
        window.addEventListener('popstate', () => {
            const url = new URL(window.location.href);
            input.value = url.searchParams.get('chemical') || '';
            concentration.value = '';
            const restoredResin = url.searchParams.get('resin') || '';
            resin.value = [...resin.options].some((option) => option.value === restoredResin) ? restoredResin : '';
            runSearch({
                append: false,
                history: 'none',
                concentration: url.searchParams.get('concentration') || ''
            });
        });

        const initialUrl = new URL(window.location.href);
        const initialChemical = initialUrl.searchParams.get('chemical') || '';
        if (initialChemical) {
            input.value = initialChemical;
            const initialResin = initialUrl.searchParams.get('resin') || '';
            resin.value = [...resin.options].some((option) => option.value === initialResin) ? initialResin : '';
            runSearch({
                append: false,
                history: 'none',
                concentration: initialUrl.searchParams.get('concentration') || ''
            });
        } else {
            setStatus(`Enter at least ${minimumCharacters} characters.`);
        }
    }

    function initialize() {
        document.querySelectorAll('[data-derakane-search]').forEach(initSearch);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
    else initialize();
}());
