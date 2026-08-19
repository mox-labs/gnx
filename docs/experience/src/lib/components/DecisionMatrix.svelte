<script lang="ts">
	// The dossier grammar's comparison figure (G5): options × reader-questions in one
	// simultaneous-perception table, with a per-row (or per-cell) epistemic mark so the
	// reader sees at a glance which entries are measured, standard, or judgment. There is
	// NEVER an aggregate/total row — the grammar forbids collapsing a decision to a score.
	// Marks are colour-AND-glyph paired, never colour alone.

	type Kind = 'measured' | 'standard' | 'judgment';
	interface Row {
		label: string;
		kind?: Kind;
		weight?: string;
	}
	interface CellObj {
		v: string;
		kind?: Kind;
	}
	interface MatrixData {
		title?: string;
		rows: Row[];
		cols: string[];
		cells: (string | CellObj)[][];
		note?: string;
		__malformed?: string;
	}

	let { data }: { data: MatrixData } = $props();

	const problems = $derived.by(() => {
		const out: string[] = [];
		if (data?.__malformed) return [`malformed JSON — ${data.__malformed}`];
		if (!Array.isArray(data?.rows) || data.rows.length === 0) out.push('rows[] required');
		if (!Array.isArray(data?.cols) || data.cols.length === 0) out.push('cols[] required');
		if (!Array.isArray(data?.cells)) out.push('cells[][] required');
		return out;
	});
	const invalid = $derived(problems.length > 0);

	const KINDS: Record<Kind, { glyph: string; word: string }> = {
		measured: { glyph: '●', word: 'measured' },
		standard: { glyph: '◆', word: 'standard' },
		judgment: { glyph: '◇', word: 'judgment' }
	};
	const usedKinds = $derived.by(() => {
		const s = new Set<Kind>();
		for (const r of data?.rows ?? []) if (r.kind) s.add(r.kind);
		for (const row of data?.cells ?? [])
			for (const c of row) if (c && typeof c === 'object' && c.kind) s.add(c.kind);
		return (Object.keys(KINDS) as Kind[]).filter((k) => s.has(k));
	});

	function cellText(c: string | CellObj | undefined): string {
		if (c == null) return '';
		return typeof c === 'object' ? c.v : c;
	}
	function cellKind(c: string | CellObj | undefined, row: Row): Kind | undefined {
		if (c && typeof c === 'object' && c.kind) return c.kind;
		return row?.kind;
	}
</script>

<figure class="dm">
	{#if data?.title}<figcaption class="title">{data.title}</figcaption>{/if}

	{#if invalid}
		<div class="err">
			<b>decision-matrix — invalid</b>
			<ul>
				{#each problems as p (p)}<li>{p}</li>{/each}
			</ul>
		</div>
	{:else}
		<div class="scroll">
			<table>
				<thead>
					<tr>
						<th scope="col" class="corner"></th>
						{#each data.cols as col (col)}<th scope="col">{col}</th>{/each}
					</tr>
				</thead>
				<tbody>
					{#each data.rows as row, ri (row.label)}
						<tr>
							<th scope="row">
								<span class="rlabel">{row.label}</span>
								{#if row.weight}<span class="weight">{row.weight}</span>{/if}
								{#if row.kind}
									<span class="mark {row.kind}" title={KINDS[row.kind].word}>
										<span class="glyph">{KINDS[row.kind].glyph}</span>{KINDS[row.kind].word}
									</span>
								{/if}
							</th>
							{#each data.cols as _col, ci (ci)}
								{@const c = data.cells?.[ri]?.[ci]}
								{@const k = cellKind(c, row)}
								<td>
									<span class="cval">{cellText(c)}</span>
									{#if c && typeof c === 'object' && c.kind}
										<span class="mark cellmark {k}" title={KINDS[k as Kind].word}>
											<span class="glyph">{KINDS[k as Kind].glyph}</span>{KINDS[k as Kind].word}
										</span>
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		{#if data.note}<p class="note">{data.note}</p>{/if}

		{#if usedKinds.length}
			<div class="legend">
				<span class="lg-lbl">epistemic kind</span>
				{#each usedKinds as k (k)}
					<span class="mark {k}"><span class="glyph">{KINDS[k].glyph}</span>{KINDS[k].word}</span>
				{/each}
			</div>
		{/if}
	{/if}
</figure>

<style>
	.dm {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 1rem 1.1rem;
		margin: 1.6rem 0;
		background: color-mix(in srgb, var(--panel) 40%, transparent);
		font-family: var(--sans);
		font-size: 0.9rem;
	}
	.title {
		font: 600 1rem var(--sans);
		margin-bottom: 0.6rem;
	}
	.err {
		border: 1px solid var(--constraint);
		background: color-mix(in oklab, var(--constraint) 8%, transparent);
		border-radius: 6px;
		padding: 0.6rem 0.8rem;
		font-size: 0.85rem;
	}
	.err b {
		color: var(--constraint);
	}
	.err ul {
		margin: 0.3rem 0 0;
		padding-left: 1.1rem;
	}
	.scroll {
		overflow-x: auto;
	}
	table {
		border-collapse: collapse;
		width: 100%;
		font-size: 0.86rem;
	}
	th,
	td {
		border: 1px solid var(--line);
		padding: 0.45rem 0.6rem;
		text-align: left;
		vertical-align: top;
	}
	thead th {
		background: var(--panel);
		font: 600 0.84rem var(--sans);
	}
	th.corner {
		background: transparent;
		border-top: none;
		border-left: none;
	}
	th[scope='row'] {
		background: color-mix(in srgb, var(--panel) 55%, transparent);
		font-weight: 600;
	}
	.rlabel {
		display: block;
	}
	.weight {
		display: inline-block;
		font: 0.82rem var(--mono);
		color: var(--muted);
		margin-top: 0.15rem;
	}
	.cval {
		display: block;
		color: var(--fg);
		line-height: 1.45;
	}
	/* epistemic mark — glyph + word together, so meaning survives without colour */
	.mark {
		display: inline-flex;
		align-items: center;
		gap: 0.25em;
		font: 600 0.8125rem var(--mono);
		margin-top: 0.3rem;
		white-space: nowrap;
	}
	.mark .glyph {
		font-size: 0.9em;
	}
	.mark.cellmark {
		font-size: 0.8125rem;
		opacity: 0.85;
	}
	.mark.measured {
		color: var(--resolved);
	}
	.mark.standard {
		color: var(--accent);
	}
	.mark.judgment {
		color: var(--ev-weak);
	}
	.note {
		font-size: 0.84rem;
		color: var(--muted);
		margin: 0.7rem 0 0;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.9rem;
		margin-top: 0.7rem;
		padding-top: 0.6rem;
		border-top: 1px dashed var(--line);
	}
	.legend .lg-lbl {
		font: 600 0.82rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
	}
	.legend .mark {
		margin-top: 0;
	}
</style>
