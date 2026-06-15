<script lang="ts">
	// An explorable design fork: alternatives scored across criteria, with scenarios
	// that show how the right choice shifts with context. Data-driven — the markdown
	// fence carries the decision; this renders it.

	type Level = 'pass' | 'partial' | 'fail';
	interface Alt {
		id: string;
		name: string;
		sketch: string;
		recommended?: boolean;
	}
	interface Crit {
		id: string;
		name: string;
	}
	interface Scenario {
		id: string;
		name: string;
		matters: string[]; // criterion ids that dominate here
		winner: string; // alt id
		why: string;
	}
	interface Decision {
		question: string;
		context?: string;
		alternatives: Alt[];
		criteria: Crit[];
		// matrix[altId][critId] = [level, note]
		matrix: Record<string, Record<string, [Level, string]>>;
		scenarios?: Scenario[];
	}

	let { data }: { data: Decision } = $props();

	let picked = $state<string | null>(null);
	let scenario = $state<string | null>(null);

	const defaultAlt = $derived(
		data.alternatives.find((a) => a.recommended)?.id ?? data.alternatives[0]?.id ?? null
	);
	const selectedAlt = $derived(picked ?? defaultAlt);
	const activeScenario = $derived(data.scenarios?.find((s) => s.id === scenario) ?? null);
	const matters = $derived(new Set(activeScenario?.matters ?? []));

	const GLYPH: Record<Level, string> = { pass: '●', partial: '◐', fail: '○' };

	function cell(altId: string, critId: string): [Level, string] {
		return data.matrix[altId]?.[critId] ?? ['fail', ''];
	}
</script>

<div class="decision">
	<div class="q">{data.question}</div>
	{#if data.context}<p class="ctx">{data.context}</p>{/if}

	{#if data.scenarios?.length}
		<div class="scenarios">
			<span class="lbl">depends on context:</span>
			{#each data.scenarios as s (s.id)}
				<button class:on={scenario === s.id} onclick={() => (scenario = scenario === s.id ? null : s.id)}>
					{s.name}
				</button>
			{/each}
		</div>
		{#if activeScenario}
			<div class="winner">
				Under <b>{activeScenario.name.toLowerCase()}</b>:
				<b class="win">{data.alternatives.find((a) => a.id === activeScenario.winner)?.name}</b> wins —
				{activeScenario.why}
			</div>
		{/if}
	{/if}

	<div class="grid" style="grid-template-columns: minmax(8rem, 1.4fr) repeat({data.alternatives.length}, 1fr)">
		<div class="corner"></div>
		{#each data.alternatives as a (a.id)}
			<button
				class="alt-head"
				class:sel={selectedAlt === a.id}
				class:rec={a.recommended}
				class:win={activeScenario?.winner === a.id}
				onclick={() => (picked = a.id)}
			>
				{a.name}
				{#if a.recommended}<span class="tag">lean</span>{/if}
			</button>
		{/each}

		{#each data.criteria as c (c.id)}
			<div class="crit" class:dim={activeScenario && !matters.has(c.id)} class:hot={matters.has(c.id)}>
				{c.name}
			</div>
			{#each data.alternatives as a (a.id)}
				{@const [level, note] = cell(a.id, c.id)}
				<div
					class="cell {level}"
					class:dim={activeScenario && !matters.has(c.id)}
					class:colsel={selectedAlt === a.id}
					title={note}
				>
					<span class="g">{GLYPH[level]}</span>
				</div>
			{/each}
		{/each}
	</div>

	<div class="legend">
		<span><span class="g pass">●</span> holds</span>
		<span><span class="g partial">◐</span> partial</span>
		<span><span class="g fail">○</span> breaks</span>
		<span class="hint">click an alternative to read why · hover a cell for the detail</span>
	</div>

	{#if selectedAlt}
		{@const a = data.alternatives.find((x) => x.id === selectedAlt)}
		{#if a}
			<div class="detail">
				<div class="detail-head">{a.name}{#if a.recommended}<span class="tag">lean</span>{/if}</div>
				<p class="sketch">{a.sketch}</p>
				<ul>
					{#each data.criteria as c (c.id)}
						{@const [level, note] = cell(a.id, c.id)}
						<li class={level} class:hot={matters.has(c.id)}>
							<span class="g {level}">{GLYPH[level]}</span>
							<b>{c.name}.</b>
							{note}
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	{/if}
</div>

<style>
	.decision {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 1rem 1.1rem;
		margin: 1.4rem 0;
		background: color-mix(in srgb, var(--panel) 40%, transparent);
		font-family: var(--sans);
	}
	.q {
		font: 600 1rem var(--sans);
		margin-bottom: 0.3rem;
	}
	.ctx {
		font-size: 0.86rem;
		color: var(--muted);
		margin: 0 0 0.8rem;
	}
	.scenarios {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
		margin-bottom: 0.6rem;
	}
	.scenarios .lbl {
		font: 0.72rem var(--mono);
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.scenarios button {
		font: 0.78rem var(--sans);
		border: 1px solid var(--line);
		border-radius: 999px;
		background: var(--bg);
		color: var(--fg);
		padding: 0.2rem 0.7rem;
		cursor: pointer;
	}
	.scenarios button.on {
		border-color: var(--accent);
		background: color-mix(in srgb, var(--accent) 12%, transparent);
		color: var(--accent);
	}
	.winner {
		font-size: 0.84rem;
		color: var(--fg);
		background: color-mix(in srgb, var(--accent) 7%, transparent);
		border-left: 3px solid var(--accent);
		padding: 0.45rem 0.7rem;
		border-radius: 0 5px 5px 0;
		margin-bottom: 0.8rem;
	}
	.winner .win {
		color: var(--accent);
	}
	.grid {
		display: grid;
		gap: 1px;
		background: var(--line);
		border: 1px solid var(--line);
		border-radius: 6px;
		overflow: hidden;
	}
	.corner {
		background: var(--panel);
	}
	.alt-head {
		background: var(--panel);
		border: none;
		border-bottom: 2px solid transparent;
		color: var(--fg);
		font: 600 0.78rem var(--sans);
		padding: 0.45rem 0.5rem;
		cursor: pointer;
		text-align: left;
		line-height: 1.25;
	}
	.alt-head:hover {
		background: color-mix(in srgb, var(--accent) 6%, var(--panel));
	}
	.alt-head.sel {
		border-bottom-color: var(--accent);
	}
	.alt-head.rec {
		color: var(--accent);
	}
	.alt-head.win {
		background: color-mix(in srgb, var(--accent) 14%, var(--panel));
	}
	.tag {
		font: 0.58rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.06em;
		border: 1px solid currentColor;
		border-radius: 3px;
		padding: 0 0.25em;
		margin-left: 0.3em;
		vertical-align: middle;
	}
	.crit {
		background: var(--bg);
		font-size: 0.78rem;
		padding: 0.4rem 0.55rem;
		display: flex;
		align-items: center;
	}
	.crit.hot {
		font-weight: 600;
	}
	.crit.dim {
		opacity: 0.4;
	}
	.cell {
		background: var(--bg);
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0.4rem;
	}
	.cell.colsel {
		background: color-mix(in srgb, var(--accent) 5%, var(--bg));
	}
	.cell.dim {
		opacity: 0.35;
	}
	.g {
		font-size: 0.95rem;
		line-height: 1;
	}
	.g.pass,
	.cell.pass .g {
		color: var(--resolved);
	}
	.g.partial,
	.cell.partial .g {
		color: #b8860b;
	}
	.g.fail,
	.cell.fail .g {
		color: var(--muted);
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.9rem;
		align-items: center;
		font: 0.74rem var(--sans);
		color: var(--muted);
		margin-top: 0.55rem;
	}
	.legend .hint {
		margin-left: auto;
		font-style: italic;
	}
	.detail {
		margin-top: 0.8rem;
		border-top: 1px dashed var(--line);
		padding-top: 0.7rem;
	}
	.detail-head {
		font: 600 0.88rem var(--sans);
	}
	.sketch {
		font-size: 0.83rem;
		color: var(--muted);
		margin: 0.25rem 0 0.5rem;
	}
	.detail ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.detail li {
		font-size: 0.83rem;
		line-height: 1.45;
		padding-left: 0.2rem;
	}
	.detail li .g {
		margin-right: 0.3rem;
	}
	.detail li.hot {
		background: color-mix(in srgb, var(--accent) 6%, transparent);
		border-radius: 4px;
		padding: 0.1rem 0.3rem;
	}
</style>
