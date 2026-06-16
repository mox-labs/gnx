<script lang="ts">
	let { data } = $props();

	const CRITERIA = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'];
	const LABEL: Record<string, string> = {
		A: 'Voice',
		B: 'No tells',
		C: 'No register-announcing',
		D: 'Factual',
		E: 'Propagation',
		F: 'Tightness',
		G: 'Legibility',
		H: 'Maturity honesty',
		I: 'Task orientation',
		J: 'Grammar accuracy'
	};
	// Hard gates per the register profiles. A/C/D apply everywhere; H/J gate the
	// public guides + reference; G/I gate by context (value claims / User Guide).
	const HARD = new Set(['A', 'C', 'D', 'H', 'J']);

	function cls(letter: string, score: number) {
		if (score >= 4) return 'ok';
		if (score === 3) return 'mid';
		return HARD.has(letter) ? 'gate-fail' : 'fail';
	}
	const docName = (f: string) => f.replace(/^\d+-/, '').replace(/\.md$/, '').replace(/_/g, '');
	const slug = (f: string) =>
		f.startsWith('_') ? '' : '/docs/' + f.replace(/^\d+-/, '').replace(/\.md$/, '');
</script>

<svelte:head><title>Evaluation · gnx genesis</title></svelte:head>

<div class="doc">
	<h1>Evaluation</h1>

	<p>
		Every doc was ship-gated by an out-of-family evaluator (gemini, a different model family from
		the optimizer) against the <a href="/docs/rubric">rubric</a>. This is the result. The scores
		are gemini's, not the author's — the point of an out-of-family gate is that the maker doesn't
		grade its own work.
	</p>

	<p class="muted">
		Read the rows for the rubric, not just the docs. A verdict you disagree with means the rubric
		needs adjusting, not the doc. Three RETURNs (09, 10, 06) were hand-fixed after this gate for the
		flagged factual and redundancy items; 01 and 12 carry harsh non-gate scores where the prose is
		correct (01's triad line is canonical compression; 12's subject <em>is</em> the public boundary).
	</p>

	{#if data.evaluation.length === 0}
		<p class="muted">No evaluation on record. Run the ship-gate to populate this.</p>
	{:else}
		<table class="scorecard">
			<thead>
				<tr>
					<th>Doc</th>
					<th>Verdict</th>
					{#each CRITERIA as c (c)}
						<th class="crit" title={LABEL[c]} class:hard={HARD.has(c)}>{c}</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each data.evaluation as row (row.file)}
					<tr>
						<td class="docname">
							{#if slug(row.file)}<a href={slug(row.file)}>{docName(row.file)}</a>
							{:else}{docName(row.file)}{/if}
						</td>
						<td><span class="verdict {row.verdict.toLowerCase()}">{row.verdict}</span></td>
						{#each CRITERIA as c (c)}
							<td class="score {row.scores[c] ? cls(c, row.scores[c]) : 'na'}">
								{row.scores[c] ?? '·'}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>

		<div class="legend">
			<span><b>Hard gates</b> (A, C, D, and G on value claims): must score ≥ 4.</span>
			<span class="k"><i class="sw ok"></i>≥4</span>
			<span class="k"><i class="sw mid"></i>3</span>
			<span class="k"><i class="sw fail"></i>≤2</span>
			<span class="k"><i class="sw gate-fail"></i>hard-gate fail</span>
		</div>

		<h2>How to read this</h2>
		<p>
			The rubric grew. Criteria <b>G (legibility)</b>, <b>H (maturity honesty)</b>,
			<b>I (task orientation)</b>, and <b>J (grammar accuracy)</b> were added after the internal
			dossier (01–13) was first gated — those rows show <span class="na-inline">·</span> in the
			new columns because they predate the criteria. The public Start pages are gated against the
			register-aware profile, so they carry G–J scores; a column is blank where the criterion does
			not apply to that register. That is the loop: a doc surfaces a failure mode, the rubric grows
			a criterion, the next gate scores against it. The rubric is the artifact under review here as
			much as the docs are.
		</p>
	{/if}

	<p class="back"><a href="/docs/rubric">→ Read the rubric</a></p>
</div>

<style>
	.scorecard {
		border-collapse: collapse;
		font: 0.85rem var(--sans);
		margin: 1.2rem 0;
	}
	.scorecard th,
	.scorecard td {
		border: 1px solid var(--line);
		padding: 0.4rem 0.55rem;
		text-align: center;
	}
	.scorecard th {
		background: var(--panel);
		font-weight: 600;
	}
	.scorecard th.crit {
		width: 2.1rem;
		font-family: var(--mono);
		font-size: 0.78rem;
	}
	.scorecard th.hard {
		color: var(--accent);
	}
	td.docname {
		text-align: left;
		font-family: var(--mono);
		font-size: 0.78rem;
		white-space: nowrap;
	}
	td.docname a {
		text-decoration: none;
	}
	.verdict {
		font: 0.7rem var(--mono);
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.verdict.ship {
		color: var(--resolved);
		border: 1px solid var(--resolved);
	}
	.verdict.tighten {
		color: var(--ev-weak);
		border: 1px solid var(--ev-weak);
	}
	.verdict.return {
		color: var(--constraint);
		border: 1px solid var(--constraint);
	}
	td.score {
		font-family: var(--mono);
		font-weight: 600;
	}
	td.score.ok {
		color: var(--resolved);
	}
	td.score.mid {
		color: var(--ev-weak);
	}
	td.score.fail {
		color: var(--muted);
	}
	td.score.gate-fail {
		color: #fff;
		background: var(--constraint);
	}
	td.score.na {
		color: var(--line);
	}
	.na-inline {
		font-family: var(--mono);
		color: var(--muted);
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		align-items: center;
		font: 0.78rem var(--sans);
		color: var(--muted);
		margin: 0.5rem 0 1rem;
	}
	.legend .k {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.sw {
		width: 0.8rem;
		height: 0.8rem;
		border-radius: 2px;
		display: inline-block;
	}
	.sw.ok {
		background: var(--resolved);
	}
	.sw.mid {
		background: var(--ev-weak);
	}
	.sw.fail {
		background: var(--muted);
	}
	.sw.gate-fail {
		background: var(--constraint);
	}
	.back {
		margin-top: 2rem;
		font: 0.85rem var(--mono);
	}
	.back a {
		text-decoration: none;
		color: var(--accent);
	}
</style>
