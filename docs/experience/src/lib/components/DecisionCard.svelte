<script lang="ts">
	// The dossier grammar's DecisionCard (G2/G3/G6/G7). A decision is a first-class
	// object: stakes (never preference), options with mandatory consequences held
	// symmetric, assumptions with failure-implications, indicators, two-axis
	// calibration, and an attributed recommendation kept in a separate stratum.
	// The commitment surface at the bottom solicits a ruling — silence never closes a
	// gating decision. Friction is engineered only at irreversibility: a one-way door
	// carries a heavier warning and requires a note on any ruling.
	import { invalidateAll } from '$app/navigation';
	import { browser } from '$app/environment';
	import type { Thread } from '$lib/server/feedback';

	interface Option {
		id: string;
		label: string;
		consequence: string;
	}
	interface Assumption {
		text: string;
		ifWrong: string;
	}
	interface Calibration {
		confidence?: 'low' | 'moderate' | 'high';
		basis?: string;
		likelihood?: string;
	}
	interface Recommendation {
		by: string;
		lean: string;
		basis?: string;
	}
	interface DecisionData {
		id: string;
		title: string;
		context: string;
		reversibility: 'one-way' | 'two-way';
		gates?: string[];
		calibration?: Calibration;
		options: Option[];
		assumptions?: Assumption[];
		indicators?: string[];
		recommendation?: Recommendation;
		__malformed?: string;
	}

	let {
		doc,
		data,
		threads = []
	}: { doc: string; data: DecisionData; threads?: Thread[] } = $props();

	// ---- validation (visible on the reading surface, never a crash) ----
	const problems = $derived.by(() => {
		const out: string[] = [];
		if (data?.__malformed) {
			out.push(`malformed JSON — ${data.__malformed}`);
			return out;
		}
		if (!data?.title) out.push('missing title');
		if (!data?.context) out.push('missing context (the stakes)');
		if (data?.reversibility !== 'one-way' && data?.reversibility !== 'two-way')
			out.push('reversibility must be "one-way" or "two-way"');
		const opts = Array.isArray(data?.options) ? data.options : [];
		if (opts.length < 2) out.push('at least two options are required');
		opts.forEach((o, i) => {
			if (!o || typeof o !== 'object') out.push(`option ${i + 1} is not an object`);
			else if (!o.consequence?.trim())
				out.push(`option "${o.label ?? o.id ?? i + 1}" is missing its consequence (required)`);
		});
		return out;
	});
	const invalid = $derived(problems.length > 0);

	const options = $derived(Array.isArray(data?.options) ? data.options : []);
	const oneWay = $derived(data?.reversibility === 'one-way');
	const labelOf = $derived((id: string) => options.find((o) => o.id === id)?.label ?? id);

	// ---- ruled / open state, derived from the threads this bid already carries ----
	const bid = $derived('decision:' + (data?.id ?? ''));

	interface Ruling {
		verb: 'ratify' | 'object' | 'redirect';
		option?: string;
		note: string;
		author: string;
		ts: string;
	}
	function parseRuling(t: Thread): Ruling | null {
		const first = t.items[0];
		if (!first || !first.body.startsWith('RULING')) return null;
		// latest event is a reopen ⇒ the ruling was pulled back; treat as open again
		const last = t.items[t.items.length - 1];
		if (last.kind === 'reopen') return null;
		const rest = first.body.slice('RULING'.length).trim();
		const [head, ...noteParts] = rest.split('—');
		const note = noteParts.join('—').trim();
		const h = head.trim();
		if (h.startsWith('ratify:'))
			return { verb: 'ratify', option: h.slice('ratify:'.length).trim(), note, author: first.author, ts: first.ts };
		if (h.startsWith('object')) return { verb: 'object', note, author: first.author, ts: first.ts };
		if (h.startsWith('redirect')) return { verb: 'redirect', note, author: first.author, ts: first.ts };
		return { verb: 'ratify', note, author: first.author, ts: first.ts };
	}
	const ruling = $derived.by(() => {
		const rulings = threads
			.map(parseRuling)
			.filter((r): r is Ruling => r !== null)
			.sort((a, b) => a.ts.localeCompare(b.ts));
		return rulings.at(-1) ?? null;
	});

	// ---- commitment surface (reuses the site's comment client + author handling) ----
	let author = $state((browser && localStorage.getItem('gnx-fb-author')) || 'yash');
	let note = $state('');
	let busy = $state(false);
	let offline = $state(false);
	let reRule = $state(false);

	async function rule(body: string) {
		if (!browser) return;
		busy = true;
		try {
			localStorage.setItem('gnx-fb-author', author);
			const res = await fetch('/api/feedback', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					doc,
					author,
					kind: 'comment',
					bid,
					quote: data.title,
					body
				})
			});
			if (!res.ok) throw new Error(await res.text());
			note = '';
			reRule = false;
			await invalidateAll();
		} catch {
			// public/static build has no feedback endpoint — degrade, never crash
			offline = true;
		} finally {
			busy = false;
		}
	}

	// a note is mandatory for object / redirect, and for ANY ruling on a one-way door
	const noteRequired = $derived((verb: 'ratify' | 'object' | 'redirect') =>
		verb !== 'ratify' || oneWay
	);
	function canRule(verb: 'ratify' | 'object' | 'redirect'): boolean {
		return !busy && !offline && (!noteRequired(verb) || note.trim().length > 0);
	}
	function ratify(id: string) {
		rule(`RULING ratify:${id} — ${note.trim()}`);
	}
	function object() {
		rule(`RULING object — ${note.trim()}`);
	}
	function redirect() {
		rule(`RULING redirect — ${note.trim()}`);
	}
</script>

<div class="dc" class:one-way={oneWay}>
	{#if invalid}
		<div class="err">
			<b>decision block — invalid</b>
			<ul>
				{#each problems as p (p)}<li>{p}</li>{/each}
			</ul>
		</div>
	{/if}

	{#if !data?.__malformed}
		<header class="head">
			<div class="title-row">
				<h4 class="title">{data.title ?? 'untitled decision'}</h4>
				<span
					class="rev {oneWay ? 'one' : 'two'}"
					title={oneWay
						? 'One-way door — hard to reverse. Rulings here require a recorded note.'
						: 'Two-way door — reversible.'}
				>
					{oneWay ? '⚠ one-way' : '⇄ two-way'}
				</span>
				<span class="state {ruling ? 'ruled' : 'open'}">{ruling ? 'ruled' : 'open'}</span>
			</div>
			{#if data.gates?.length}
				<div class="gates">
					gated by:{#each data.gates as g (g)}<code>{g}</code>{/each}
				</div>
			{/if}
			{#if data.context}<p class="ctx">{data.context}</p>{/if}
		</header>

		{#if oneWay}
			<div class="friction">
				One-way door — engineered friction. A ruling here is hard to reverse; a note is required.
			</div>
		{/if}

		<!-- options: symmetric, no ranked / default / highlighted row (the recommendation
		     lives in its own stratum below and never styles a row) -->
		{#if options.length}
			<table class="options">
				<thead>
					<tr><th scope="col">option</th><th scope="col">consequence</th></tr>
				</thead>
				<tbody>
					{#each options as o (o.id)}
						<tr>
							<th scope="row">{o.label}</th>
							<td class:missing={!o.consequence?.trim()}>
								{o.consequence?.trim() || '⚠ consequence required'}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}

		{#if data.assumptions?.length}
			<section class="strat">
				<div class="lbl">assumptions</div>
				<ul class="assumptions">
					{#each data.assumptions as a (a.text)}
						<li><span class="atext">{a.text}</span> <span class="arrow">→ if wrong:</span> {a.ifWrong}</li>
					{/each}
				</ul>
			</section>
		{/if}

		{#if data.indicators?.length}
			<section class="strat">
				<div class="lbl">what would change this</div>
				<ul class="indicators">
					{#each data.indicators as ind (ind)}<li>{ind}</li>{/each}
				</ul>
			</section>
		{/if}

		{#if data.calibration}
			<section class="strat calibration">
				<div class="chips">
					{#if data.calibration.confidence}
						<span class="chip conf {data.calibration.confidence}">
							confidence: {data.calibration.confidence}
						</span>
					{/if}
					{#if data.calibration.likelihood}
						<span class="chip like">likelihood: {data.calibration.likelihood}</span>
					{/if}
				</div>
				{#if data.calibration.basis}<p class="basis">basis — {data.calibration.basis}</p>{/if}
			</section>
		{/if}

		{#if data.recommendation}
			<aside class="rec">
				<span class="rec-tag">recommendation</span>
				<span class="rec-by">{data.recommendation.by}</span>: leans
				<b>{labelOf(data.recommendation.lean)}</b>.
				{#if data.recommendation.basis}<span class="rec-basis">{data.recommendation.basis}</span>{/if}
			</aside>
		{/if}

		<!-- commitment surface (G6): the apparatus solicits a ruling -->
		<div class="menu">
			{#if ruling}
				<div class="ruled-note">
					<b>Ruled:</b>
					{#if ruling.verb === 'ratify'}ratified <b>{labelOf(ruling.option ?? '')}</b>
					{:else if ruling.verb === 'object'}objected
					{:else}redirected{/if}
					— {ruling.author} · {new Date(ruling.ts).toLocaleString()}
					{#if ruling.note}<span class="rn">“{ruling.note}”</span>{/if}
				</div>
			{/if}

			{#if offline}
				<p class="quiet">Feedback isn't available in this build — the ruling menu is read-only here.</p>
			{/if}

			<details bind:open={reRule} class="menu-body" class:standalone={!ruling}>
				{#if ruling}<summary>re-rule</summary>{/if}
				<div class="controls" class:disabled={offline}>
					<textarea
						bind:value={note}
						placeholder={oneWay
							? 'note required — one-way door'
							: 'note (required to object or redirect)'}
						disabled={offline || busy}
					></textarea>
					<div class="actions">
						<label class="who">
							<span>as</span>
							<input type="text" bind:value={author} disabled={offline || busy} />
						</label>
						{#each options as o (o.id)}
							<button
								class="ratify"
								disabled={!canRule('ratify')}
								title={oneWay && !note.trim() ? 'a note is required on a one-way door' : ''}
								onclick={() => ratify(o.id)}
							>
								Ratify: {o.label}
							</button>
						{/each}
						<button class="object" disabled={!canRule('object')} onclick={object}>Object</button>
						<button class="redirect" disabled={!canRule('redirect')} onclick={redirect}>Redirect</button>
					</div>
				</div>
			</details>
		</div>
	{/if}
</div>

<style>
	.dc {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 1rem 1.1rem;
		margin: 1.6rem 0;
		background: color-mix(in srgb, var(--panel) 40%, transparent);
		font-family: var(--sans);
		font-size: 0.9rem;
		line-height: 1.55;
	}
	.dc.one-way {
		border-color: color-mix(in oklab, var(--constraint) 45%, var(--line));
		box-shadow: inset 3px 0 0 var(--constraint);
	}
	.err {
		border: 1px solid var(--constraint);
		background: color-mix(in oklab, var(--constraint) 8%, transparent);
		border-radius: 6px;
		padding: 0.6rem 0.8rem;
		margin-bottom: 0.9rem;
		font-size: 0.85rem;
	}
	.err b {
		color: var(--constraint);
	}
	.err ul {
		margin: 0.3rem 0 0;
		padding-left: 1.1rem;
	}

	.head {
		margin-bottom: 0.7rem;
	}
	.title-row {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.title {
		font: 600 1rem var(--sans);
		margin: 0;
		flex: 1;
		min-width: 12rem;
	}
	.rev {
		font: 600 0.82rem var(--mono);
		letter-spacing: 0.02em;
		padding: 0.1rem 0.5rem;
		border-radius: 4px;
		border: 1px solid var(--line);
		color: var(--muted);
		white-space: nowrap;
	}
	.rev.two {
		color: var(--accent);
		border-color: color-mix(in oklab, var(--accent) 45%, var(--line));
	}
	/* one-way is the engineered-friction mark: heavier, warning-colored, filled */
	.rev.one {
		color: #fff;
		background: var(--constraint);
		border-color: var(--constraint);
		font-weight: 700;
	}
	.state {
		font: 0.82rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
	}
	.state.open {
		color: var(--accent);
		border: 1px solid color-mix(in oklab, var(--accent) 45%, var(--line));
	}
	.state.ruled {
		color: var(--resolved);
		border: 1px solid color-mix(in oklab, var(--resolved) 45%, var(--line));
	}
	.gates {
		font: 0.82rem var(--mono);
		color: var(--muted);
		margin-top: 0.4rem;
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
		align-items: baseline;
	}
	.gates code {
		font-size: 0.82rem;
		background: var(--panel);
		padding: 0.05em 0.35em;
		border-radius: 3px;
	}
	.ctx {
		margin: 0.55rem 0 0;
		color: var(--fg);
	}
	.friction {
		font: 0.82rem var(--sans);
		color: var(--constraint);
		background: color-mix(in oklab, var(--constraint) 8%, transparent);
		border-left: 3px solid var(--constraint);
		border-radius: 0 5px 5px 0;
		padding: 0.45rem 0.7rem;
		margin: 0 0 0.9rem;
	}

	.options {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.88rem;
		margin: 0.3rem 0 1rem;
	}
	.options th[scope='col'] {
		text-align: left;
		font: 600 0.82rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
		border-bottom: 1px solid var(--line);
		padding: 0.35rem 0.6rem;
	}
	.options th[scope='row'] {
		text-align: left;
		font: 600 0.88rem var(--sans);
		vertical-align: top;
		padding: 0.45rem 0.6rem;
		width: 34%;
		white-space: normal;
	}
	.options td {
		vertical-align: top;
		padding: 0.45rem 0.6rem;
		color: var(--fg);
	}
	.options tbody tr + tr th,
	.options tbody tr + tr td {
		border-top: 1px solid var(--line);
	}
	.options td.missing {
		color: var(--constraint);
		font-style: italic;
	}

	.strat,
	.strat.calibration {
		margin: 0.7rem 0;
	}
	.lbl {
		font: 600 0.82rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
		margin-bottom: 0.3rem;
	}
	.assumptions,
	.indicators {
		margin: 0;
		padding-left: 1.1rem;
	}
	.assumptions li,
	.indicators li {
		margin: 0.25rem 0;
		font-size: 0.86rem;
	}
	.assumptions .atext {
		font-weight: 600;
	}
	.assumptions .arrow {
		color: var(--muted);
		font-family: var(--mono);
		font-size: 0.82rem;
	}

	/* two-axis calibration — confidence and likelihood are separate chips, never blended */
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.chip {
		font: 600 0.82rem var(--mono);
		padding: 0.12rem 0.5rem;
		border-radius: 999px;
		border: 1px solid var(--line);
		color: var(--muted);
	}
	.chip.conf.high {
		color: var(--resolved);
		border-color: color-mix(in oklab, var(--resolved) 50%, var(--line));
	}
	.chip.conf.moderate {
		color: var(--accent);
		border-color: color-mix(in oklab, var(--accent) 50%, var(--line));
	}
	.chip.conf.low {
		color: var(--constraint);
		border-color: color-mix(in oklab, var(--constraint) 50%, var(--line));
	}
	.chip.like {
		color: var(--fg);
		border-style: dashed;
	}
	.basis {
		margin: 0.4rem 0 0;
		font-size: 0.84rem;
		color: var(--muted);
	}

	/* recommendation — a separate, bordered, signed stratum; never styles an option */
	.rec {
		margin: 1rem 0 0;
		border: 1px dashed color-mix(in oklab, var(--accent) 55%, var(--line));
		border-radius: 6px;
		background: color-mix(in srgb, var(--accent) 5%, transparent);
		padding: 0.6rem 0.8rem;
		font-size: 0.86rem;
	}
	.rec-tag {
		font: 700 0.82rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--accent);
		margin-right: 0.45rem;
	}
	.rec-by {
		font-weight: 600;
	}
	.rec-basis {
		color: var(--muted);
	}

	.menu {
		margin-top: 1.1rem;
		border-top: 1px solid var(--line);
		padding-top: 0.8rem;
	}
	.ruled-note {
		font-size: 0.86rem;
		margin-bottom: 0.5rem;
	}
	.ruled-note b {
		color: var(--resolved);
	}
	.ruled-note .rn {
		color: var(--muted);
		font-style: italic;
		margin-left: 0.3rem;
	}
	.quiet {
		font-size: 0.82rem;
		color: var(--muted);
		margin: 0 0 0.5rem;
	}
	.menu-body.standalone > summary {
		display: none;
	}
	.menu-body > summary {
		cursor: pointer;
		font: 600 0.82rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
		margin-bottom: 0.5rem;
	}
	.controls.disabled {
		opacity: 0.55;
	}
	.controls textarea {
		width: 100%;
		min-height: 3rem;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		font: 0.85rem/1.5 var(--sans);
		padding: 0.45rem 0.6rem;
		resize: vertical;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		align-items: center;
		margin-top: 0.5rem;
	}
	.who {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font: 0.82rem var(--mono);
		color: var(--muted);
	}
	.who input {
		width: 6rem;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		font: 0.82rem var(--mono);
		padding: 0.25rem 0.45rem;
	}
	.actions button {
		font: 600 0.82rem var(--mono);
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		padding: 0.3rem 0.7rem;
		cursor: pointer;
	}
	.actions button:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.actions button.ratify:hover:not(:disabled) {
		border-color: var(--resolved);
		color: var(--resolved);
	}
	.actions button.object:hover:not(:disabled),
	.actions button.redirect:hover:not(:disabled) {
		border-color: var(--constraint);
		color: var(--constraint);
	}
	.actions button:disabled {
		opacity: 0.4;
		cursor: default;
	}
</style>
