<script lang="ts">
	// A client-side model of gnx's composition: components declare consumes/produces,
	// the DAG is compiled from declarations alone (no execution), topology is validated,
	// parallel batches fall out. This is "compose without running them" made tactile.
	import { compile } from '$lib/dag';

	interface Comp {
		id: string;
		label: string;
		lang: 'py' | 'rust' | 'ts' | 'md';
		consumes: string[];
		produces: string[];
		note?: string;
		removable?: boolean;
		enabled: boolean;
	}

	const seed: Comp[] = [
		{ id: 'probe', label: 'probe', lang: 'md', consumes: [], produces: ['probe.stimulus'], note: 'a task file', enabled: true },
		{ id: 'subject', label: 'subject', lang: 'md', consumes: [], produces: ['subject.config'], note: 'the thing under test', enabled: true },
		{ id: 'ix.trial', label: 'ix.trial', lang: 'py', consumes: ['probe.stimulus', 'subject.config'], produces: ['trial.observation'], note: 'runs the subject on the probe', enabled: true },
		{ id: 'ix.sensor.activation', label: 'ix.sensor.activation', lang: 'py', consumes: ['trial.observation'], produces: ['sensor.reading'], note: 'a sensor (did the skill fire?)', enabled: true }
	];

	let comps = $state<Comp[]>(seed.map((c) => ({ ...c })));
	let nextId = $state(1);

	function parseList(s: string): string[] {
		return s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
	}

	function addRubrix() {
		if (comps.some((c) => c.id === 'rubrix')) return;
		comps.push({
			id: 'rubrix',
			label: 'rubrix',
			lang: 'py',
			consumes: ['trial.observation'],
			produces: ['rubric.score'],
			note: 'a sensor stacked on ix — composes a judge agent (matrix.agent.claude)',
			removable: true,
			enabled: true
		});
	}

	function addBlank() {
		const id = `component-${nextId++}`;
		comps.push({ id, label: id, lang: 'py', consumes: ['trial.observation'], produces: [`${id}.out`], note: 'your component', removable: true, enabled: true });
	}

	function remove(id: string) {
		comps = comps.filter((c) => c.id !== id);
	}

	// ---- the DAG compiler (declarations only) — shared with the catalog view ----
	const compiled = $derived(compile(comps.filter((c) => c.enabled)));

	const byId = $derived(Object.fromEntries(comps.map((c) => [c.id, c])));
	const LANG: Record<string, string> = { py: 'Python', rust: 'Rust', ts: 'TS', md: 'file' };
</script>

<div class="composer">
	<div class="cards">
		{#each comps as c (c.id)}
			<div class="card" class:off={!c.enabled}>
				<div class="card-head">
					<input class="lbl" bind:value={c.label} aria-label="name" />
					<span class="lang {c.lang}">{LANG[c.lang]}</span>
					<label class="tog" title="enable / disable">
						<input type="checkbox" bind:checked={c.enabled} />
					</label>
					{#if c.removable}<button class="rm" title="remove" onclick={() => remove(c.id)}>×</button>{/if}
				</div>
				{#if c.note}<div class="note">{c.note}</div>{/if}
				<div class="ports">
					<label>consumes
						<input
							value={c.consumes.join(' ')}
							oninput={(e) => (c.consumes = parseList((e.target as HTMLInputElement).value))}
							placeholder="(nothing)"
						/>
					</label>
					<label>produces
						<input
							value={c.produces.join(' ')}
							oninput={(e) => (c.produces = parseList((e.target as HTMLInputElement).value))}
							placeholder="(nothing)"
						/>
					</label>
				</div>
			</div>
		{/each}
	</div>

	<div class="palette">
		<button onclick={addRubrix} disabled={comps.some((c) => c.id === 'rubrix')}>+ stack rubrix over ix</button>
		<button onclick={addBlank}>+ add a component</button>
	</div>

	<div class="dag">
		<div class="dag-label">compiled pipeline — from declarations, nothing run</div>
		{#if compiled.errors.length}
			<ul class="errs">
				{#each compiled.errors as e (e)}<li>{e}</li>{/each}
			</ul>
			<p class="rejected">Rejected at compile time. Fix the declarations — the orchestrator never runs an invalid graph.</p>
		{:else}
			<div class="batches">
				{#each compiled.batches as batch, i (i)}
					<div class="batch">
						<div class="batch-tag">batch {i + 1}{batch.length > 1 ? ' · parallel' : ''}</div>
						<div class="nodes">
							{#each batch as id (id)}
								<div class="node">
									<span class="node-name">{byId[id].label}</span>
									{#if byId[id].consumes.length}
										<span class="flow">↑ {byId[id].consumes.join(', ')}</span>
									{/if}
								</div>
							{/each}
						</div>
					</div>
				{/each}
			</div>
			<p class="ok">Valid. {compiled.batches.length} stage{compiled.batches.length === 1 ? '' : 's'}; components in the same batch have no dependency edge, so they run in parallel.</p>
		{/if}
	</div>
</div>

<style>
	.composer {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 1rem;
		margin: 1.4rem 0;
		background: color-mix(in srgb, var(--panel) 40%, transparent);
		font-family: var(--sans);
	}
	.cards {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
		gap: 0.6rem;
	}
	.card {
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--bg);
		padding: 0.55rem 0.65rem;
	}
	.card.off {
		opacity: 0.45;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.lbl {
		flex: 1;
		min-width: 0;
		border: none;
		background: none;
		color: var(--fg);
		font: 600 0.84rem var(--mono);
		padding: 0.1rem 0;
	}
	.lbl:focus {
		outline: none;
		border-bottom: 1px solid var(--accent);
	}
	.lang {
		font: 0.62rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		padding: 0.05rem 0.35rem;
		border-radius: 3px;
		border: 1px solid var(--line);
		color: var(--muted);
	}
	.lang.rust {
		color: #b7410e;
		border-color: #b7410e;
	}
	.lang.py {
		color: #3572a5;
		border-color: #3572a5;
	}
	.tog {
		display: inline-flex;
	}
	.rm {
		border: none;
		background: none;
		color: var(--muted);
		cursor: pointer;
		font-size: 1rem;
		line-height: 1;
		padding: 0 0.2rem;
	}
	.rm:hover {
		color: var(--accent);
	}
	.note {
		font-size: 0.74rem;
		color: var(--muted);
		margin: 0.3rem 0 0.4rem;
	}
	.ports {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.ports label {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font: 0.66rem var(--mono);
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.ports input {
		flex: 1;
		min-width: 0;
		border: 1px solid var(--line);
		border-radius: 4px;
		background: var(--panel);
		color: var(--fg);
		font: 0.74rem var(--mono);
		padding: 0.2rem 0.4rem;
	}
	.ports input:focus {
		outline: none;
		border-color: var(--accent);
	}
	.palette {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin: 0.7rem 0;
	}
	.palette button {
		font: 0.78rem var(--mono);
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--bg);
		color: var(--fg);
		padding: 0.35rem 0.7rem;
		cursor: pointer;
	}
	.palette button:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.palette button:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.dag {
		border-top: 1px dashed var(--line);
		padding-top: 0.7rem;
	}
	.dag-label {
		font: 0.66rem var(--mono);
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--muted);
		margin-bottom: 0.5rem;
	}
	.batches {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.batch {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.batch-tag {
		font: 0.62rem var(--mono);
		color: var(--muted);
		width: 6.5rem;
		flex-shrink: 0;
		text-align: right;
	}
	.nodes {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.node {
		border: 1px solid var(--accent);
		border-radius: 5px;
		background: color-mix(in srgb, var(--accent) 8%, transparent);
		padding: 0.3rem 0.55rem;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
	}
	.node-name {
		font: 600 0.78rem var(--mono);
	}
	.flow {
		font: 0.64rem var(--mono);
		color: var(--muted);
	}
	.errs {
		margin: 0.2rem 0;
		padding-left: 1.1rem;
	}
	.errs li {
		font: 0.78rem var(--mono);
		color: var(--accent);
		margin: 0.2rem 0;
	}
	.rejected {
		font-size: 0.82rem;
		color: var(--accent);
	}
	.ok {
		font-size: 0.82rem;
		color: var(--muted);
		margin-top: 0.5rem;
	}
</style>
