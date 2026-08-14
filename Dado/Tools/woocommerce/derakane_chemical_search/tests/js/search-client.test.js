const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..', '..');
const CLIENT_SOURCE = fs.readFileSync(
    path.join(ROOT, 'plugin', 'frpdepot-derakane-chemical-search', 'assets', 'derakane-search.js'),
    'utf8'
);
const DATASET = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'tests', 'fixtures', 'verified', 'derakane-dataset.json'),
    'utf8'
));

function markup() {
    return `<!doctype html><html><body>
      <section data-derakane-search>
        <form data-derakane-search-form>
          <label for="chemical">Chemical name, synonym, or CAS number</label>
          <input id="chemical" data-derakane-search-input>
          <button type="submit">Search guide</button>
          <label for="concentration">Concentration (exact guide entry)</label>
          <select id="concentration" data-derakane-concentration disabled><option value="">All listed concentrations</option></select>
          <label for="resin">Resin series</label>
          <select id="resin" data-derakane-resin><option value="">All resin series</option><option value="510N">Derakane™ 510N series</option></select>
        </form>
        <p data-derakane-search-status role="status" aria-live="polite"></p>
        <div data-derakane-search-results></div>
        <button data-derakane-load-more hidden>Load more</button>
      </section>
    </body></html>`;
}

function footnotesFor(rows) {
    const ids = new Set();
    for (const row of rows) {
        row.row_footnote_ids.forEach((id) => ids.add(id));
        row.chemical_source.footnote_refs.forEach((id) => ids.add(id));
        row.cells.forEach((cell) => {
            cell.footnote_ids.forEach((id) => ids.add(id));
            cell.ratings.forEach((rating) => rating.footnote_refs.forEach((id) => ids.add(id)));
        });
    }
    return DATASET.footnotes.filter((footnote) => ids.has(footnote.id));
}

function normalize(value) {
    return String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[–—−‑]/g, '-').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
        .replace(/\s+/g, ' ');
}

function groupsFor(term) {
    const query = normalize(term);
    const rowsById = new Map(DATASET.rows.map((row) => [row.row_id, row]));
    const casById = new Map(DATASET.cas_catalog.map((entry) => [entry.cas_entry_id, entry]));
    const excludedCasKeys = new Set(DATASET.cas_catalog.filter((entry) => !entry.public_searchable)
        .flatMap((entry) => [normalize(entry.cas_raw), normalize(entry.raw_pdf_cas)]));
    if (excludedCasKeys.has(query)) return [];
    const groups = [];
    for (const entity of DATASET.search_entities) {
        const name = normalize(entity.name_key);
        const publicAliases = entity.aliases.filter((alias) =>
            !excludedCasKeys.has(normalize(alias.display)) && !excludedCasKeys.has(normalize(alias.search_key))
        );
        const aliases = publicAliases.map((alias) => alias.search_key);
        const publicCasNumbers = entity.cas_entry_ids.map((id) => casById.get(id))
            .filter((entry) => entry && entry.public_searchable && entry.normalized_cas !== null)
            .map((entry) => entry.normalized_cas);
        const secondary = [...aliases, ...publicCasNumbers].map(normalize);
        const candidates = [name, ...secondary];
        if (!candidates.some((value) => value.includes(query))) continue;
        let rank = 3;
        if (name === query) rank = 0;
        else if (secondary.includes(query)) rank = 1;
        else if (candidates.some((value) => value.startsWith(query))) rank = 2;
        groups.push({
            chemical_id: entity.entity_id,
            chemical_name: entity.display_name,
            aliases: publicAliases,
            public_cas_numbers: [...new Set(publicCasNumbers)],
            rows: entity.record_ids.map((id) => rowsById.get(id)).filter(Boolean),
            footnotes: [],
            fixture_rank: rank,
            fixture_sequence: entity.source_order,
            fixture_entity_type: entity.entity_type
        });
    }
    groups.sort((left, right) =>
        left.fixture_rank - right.fixture_rank || left.fixture_sequence - right.fixture_sequence
    );
    return groups;
}

function payloadFor(url) {
    const parsed = new URL(url);
    const chemical = parsed.searchParams.get('chemical') || '';
    const offset = Number(parsed.searchParams.get('offset') || 0);
    const concentration = parsed.searchParams.get('concentration') || '';
    const resin = parsed.searchParams.get('resin') || '';
    let groups = groupsFor(chemical).map((group) => ({
        ...group,
        rows: group.rows.map((row) => ({ ...row, cells: row.cells.map((cell) => ({ ...cell })) }))
    }));
    const concentrationOptions = [];
    for (const group of groups) {
        for (const row of group.rows) {
            if (!concentrationOptions.includes(row.concentration.display)) concentrationOptions.push(row.concentration.display);
        }
        group.rows = group.rows.filter((row) => !concentration || row.concentration.display === concentration).map((row) => ({
            ...row,
            cells: resin ? row.cells.filter((cell) => cell.resin_id === resin) : row.cells
        }));
    }
    groups = groups.filter((group) => group.rows.length ||
        (group.fixture_entity_type === 'cas_catalog_only' && !concentration && !resin));
    groups.forEach((group) => { group.footnotes = footnotesFor(group.rows); });
    const total = groups.length;
    const page = groups.slice(offset, offset + 20);
    return {
        query: chemical,
        total,
        offset,
        limit: 20,
        next_offset: offset + page.length < total ? offset + page.length : null,
        groups: page,
        concentration_options: concentrationOptions,
        resin_columns: DATASET.resin_columns.filter((column) => !resin || column.id === resin),
        source: { ...DATASET.source, dataset_sha256: 'fixture-hash' }
    };
}

function response(payload) {
    return { ok: true, status: 200, json: async () => payload };
}

function tick(milliseconds = 12) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function boot(options = {}) {
    const dom = new JSDOM(markup(), {
        url: options.url || 'https://fixture.invalid/derakane-resin-resistance-search/',
        runScripts: 'outside-only',
        pretendToBeVisual: true
    });
    const calls = [];
    dom.window.FRPDepotDerakaneConfig = {
        restUrl: 'https://fixture.invalid/wp-json/frpdepot-derakane/v1/search',
        minChars: 2,
        debounceMs: 1
    };
    dom.window.fetch = options.fetch || (async (url, fetchOptions) => {
        calls.push({ url: String(url), options: fetchOptions });
        return response(payloadFor(String(url)));
    });
    dom.window.eval(CLIENT_SOURCE);
    // JSDOM emits its own DOMContentLoaded event. Dispatching a second one would
    // initialize duplicate listeners and would not model a real page load.
    await tick();
    return { dom, calls };
}

function submit(dom, value) {
    const input = dom.window.document.querySelector('[data-derakane-search-input]');
    input.value = value;
    dom.window.document.querySelector('[data-derakane-search-form]').dispatchEvent(
        new dom.window.Event('submit', { bubbles: true, cancelable: true })
    );
}

test('restores ?chemical= and renders units, captions, scopes, footnotes, source rows, and mobile cue', async () => {
    const { dom, calls } = await boot({
        url: 'https://fixture.invalid/derakane-resin-resistance-search/?chemical=Acetone'
    });
    const document = dom.window.document;
    assert.equal(document.querySelector('[data-derakane-search-input]').value, 'Acetone');
    assert.match(calls[0].url, /chemical=Acetone/);
    assert.equal(document.querySelectorAll('.derakane-result').length, 1);
    assert.match(document.querySelector('caption').textContent, /Maximum guide service temperature.*°C\/°F/);
    assert.equal(document.querySelector('thead th').scope, 'col');
    assert.equal(document.querySelector('tbody th').scope, 'row');
    assert.match(document.querySelector('tbody td').textContent, /°C \/ .*°F/);
    assert.match(document.querySelector('.derakane-table__source').textContent, /Page 14; row 10/);
    assert.match(document.querySelector('.derakane-table__mobile-cue').textContent, /swipe horizontally/);
    assert.equal(document.querySelector('.derakane-table-wrap').getAttribute('tabindex'), '0');
    assert.match(document.querySelector('.derakane-table-wrap').getAttribute('aria-label'), /Scrollable resistance table/);
    assert.deepEqual(
        [...document.querySelectorAll('.derakane-result__footnotes li')].map((item) => item.value),
        [2, 15, 16]
    );
    dom.window.close();
});

test('history URL is pushed on submit and popstate restores and reruns the URL chemical', async () => {
    const { dom, calls } = await boot();
    const originalPush = dom.window.history.pushState.bind(dom.window.history);
    const pushed = [];
    dom.window.history.pushState = (state, title, url) => {
        pushed.push({ state, url: String(url) });
        return originalPush(state, title, url);
    };
    submit(dom, 'Acetone');
    await tick();
    assert.equal(new URL(dom.window.location.href).searchParams.get('chemical'), 'Acetone');
    assert.equal(pushed.length, 1);
    assert.equal(pushed[0].state.chemical, 'Acetone');

    dom.window.history.pushState({}, '', '?chemical=Alpha');
    dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate'));
    await tick();
    assert.equal(dom.window.document.querySelector('[data-derakane-search-input]').value, 'Alpha');
    assert.match(calls.at(-1).url, /chemical=Alpha/);
    dom.window.close();
});

test('initial URL and popstate restore exact concentration and resin filters', async () => {
    const { dom, calls } = await boot({
        url: 'https://fixture.invalid/derakane-resin-resistance-search/?chemical=Acetone&concentration=10&resin=510N'
    });
    const document = dom.window.document;
    let url = new URL(calls[0].url);
    assert.equal(url.searchParams.get('chemical'), 'Acetone');
    assert.equal(url.searchParams.get('concentration'), '10');
    assert.equal(url.searchParams.get('resin'), '510N');
    assert.equal(document.querySelector('[data-derakane-concentration]').value, '10');
    assert.equal(document.querySelector('[data-derakane-resin]').value, '510N');
    assert.equal(document.querySelectorAll('tbody tr').length, 1);
    assert.equal(document.querySelectorAll('thead th').length, 3);

    dom.window.history.pushState({}, '', '?chemical=Acetone&concentration=20');
    dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate'));
    await tick();
    url = new URL(calls.at(-1).url);
    assert.equal(url.searchParams.get('concentration'), '20');
    assert.equal(url.searchParams.has('resin'), false);
    assert.equal(document.querySelector('[data-derakane-concentration]').value, '20');
    assert.equal(document.querySelector('[data-derakane-resin]').value, '');
    dom.window.close();
});

test('new search aborts stale request and stale completion cannot overwrite newer results', async () => {
    const deferred = [];
    const fetch = (url, options) => new Promise((resolve) => deferred.push({ url: String(url), options, resolve }));
    const { dom } = await boot({ fetch });
    submit(dom, 'Acetone');
    await tick(2);
    submit(dom, 'Alpha');
    await tick(2);
    assert.equal(deferred.length, 2);
    assert.equal(deferred[0].options.signal.aborted, true);
    assert.equal(deferred[1].options.signal.aborted, false);

    deferred[1].resolve(response(payloadFor(deferred[1].url)));
    await tick();
    assert.equal(dom.window.document.querySelector('.derakane-result__title').textContent, 'Alpha');
    deferred[0].resolve(response(payloadFor(deferred[0].url)));
    await tick();
    assert.equal(dom.window.document.querySelector('.derakane-result__title').textContent, 'Alpha');
    dom.window.close();
});

test('shortening below minimum aborts and invalidates an in-flight request', async () => {
    const deferred = [];
    const fetch = (url, options) => new Promise((resolve) => deferred.push({ url: String(url), options, resolve }));
    const { dom } = await boot({ fetch });
    submit(dom, 'Acetone');
    await tick(2);
    const input = dom.window.document.querySelector('[data-derakane-search-input]');
    input.value = 'A';
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    await tick(4);
    assert.equal(deferred[0].options.signal.aborted, true);
    assert.match(dom.window.document.querySelector('[data-derakane-search-status]').textContent, /Enter at least 2/);
    deferred[0].resolve(response(payloadFor(deferred[0].url)));
    await tick();
    assert.equal(dom.window.document.querySelectorAll('.derakane-result').length, 0);
    dom.window.close();
});

test('shows at most 20 initially and Load more appends the remaining source-ordered groups', async () => {
    const { dom, calls } = await boot({
        url: 'https://fixture.invalid/derakane-resin-resistance-search/?chemical=Synthetic'
    });
    const document = dom.window.document;
    assert.equal(document.querySelectorAll('.derakane-result').length, 20);
    const loadMore = document.querySelector('[data-derakane-load-more]');
    assert.equal(loadMore.hidden, false);
    loadMore.click();
    await tick();
    assert.equal(document.querySelectorAll('.derakane-result').length, 25);
    assert.equal(loadMore.hidden, true);
    assert.match(calls.at(-1).url, /offset=20/);
    assert.equal(document.querySelector('.derakane-result__title').textContent, 'Synthetic Reagent 01');
    assert.equal([...document.querySelectorAll('.derakane-result__title')].at(-1).textContent, 'Synthetic Reagent 25');
    assert.match(document.querySelector('[data-derakane-search-status]').textContent, /Showing 25 of 25/);
    dom.window.close();
});

test('exact concentration and exact resin filters are sent without derived values', async () => {
    const { dom, calls } = await boot({
        url: 'https://fixture.invalid/derakane-resin-resistance-search/?chemical=Acetone'
    });
    const document = dom.window.document;
    const concentration = document.querySelector('[data-derakane-concentration]');
    assert.deepEqual([...concentration.options].map((option) => option.value), ['', '10', '20', '100']);
    concentration.value = '10';
    concentration.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    await tick();
    let url = new URL(calls.at(-1).url);
    assert.equal(url.searchParams.get('concentration'), '10');
    assert.equal(document.querySelectorAll('tbody tr').length, 1);

    const resin = document.querySelector('[data-derakane-resin]');
    resin.value = '510N';
    resin.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    await tick();
    url = new URL(calls.at(-1).url);
    assert.equal(url.searchParams.get('concentration'), '10');
    assert.equal(url.searchParams.get('resin'), '510N');
    assert.equal(document.querySelectorAll('thead th').length, 3); // concentration, one resin, source.
    dom.window.close();
});

test('blank is rendered as an actual blank cell with an accessible exact legend meaning', async () => {
    const { dom } = await boot({
        url: 'https://fixture.invalid/derakane-resin-resistance-search/?chemical=Acetic%20Acid'
    });
    const firstRatingCell = dom.window.document.querySelector('tbody td');
    assert.equal(firstRatingCell.textContent, '');
    assert.equal(
        firstRatingCell.getAttribute('aria-label'),
        'Blank: no data was available when the guide ratings were assigned.'
    );
    const cells = [...dom.window.document.querySelectorAll('tbody td')].map((cell) => cell.textContent);
    assert.equal(cells[1], 'NR');
    assert.equal(cells[2], 'LS');
    assert.equal(
        dom.window.document.querySelector('.derakane-result__metadata').textContent,
        'Aliases: Ethanoic Acid · CAS: 64-19-7'
    );
    dom.window.close();
});

test('renders every v2 source-special expression form without flattening it to an ordinary value', async () => {
    const { dom } = await boot();
    const document = dom.window.document;
    const expected = [
        ['Acetic Acid', 5, 'LS 80°C / 180°F'],
        ['Acetone', 4, '40°C / 100°F; LS 50°C / 120°F'],
        ['Malic Acid', 0, '75 (unit not printed)'],
        ['Ammonium Bifluoride / Sulfuric Acid', 7, '-'],
        ['Sodium Hypochlorite', 5, '510A/B: 65°C / 150°F; 510C: -']
    ];
    for (const [chemical, cellIndex, text] of expected) {
        submit(dom, chemical);
        await tick();
        assert.equal(
            document.querySelectorAll('tbody tr')[0].querySelectorAll('td')[cellIndex].textContent,
            text,
            chemical
        );
    }
    dom.window.close();
});

test('excluded raw CAS cannot be searched or displayed, while its chemical name still works', async () => {
    const { dom } = await boot();
    const document = dom.window.document;
    submit(dom, '1330-96-4');
    await tick();
    assert.equal(document.querySelectorAll('.derakane-result').length, 0);
    assert.match(document.querySelector('[data-derakane-search-status]').textContent, /No exact guide entries matched/);

    submit(dom, 'Malic Acid');
    await tick();
    assert.equal(document.querySelector('.derakane-result__title').textContent, 'Malic Acid');
    assert.doesNotMatch(document.querySelector('[data-derakane-search-results]').textContent, /1330-96-4/);
    dom.window.close();
});
