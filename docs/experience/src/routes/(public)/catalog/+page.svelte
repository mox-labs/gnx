<script lang="ts">
	import type { CatalogEntry } from './+page.server';
	import { base } from '$app/paths';

	let { data } = $props();

	// Kind is a type-level constraint, not a color. Shape-primary sigils; Skill last
	// because it's axiomatic, not pipeline. (tufte, catalog-viz-design)
	const KINDS = [
		{ kind: 'Capability', sigil: '□', gloss: 'runnable — typed ports, must expose --skill' },
		{ kind: 'Agent', sigil: '○', gloss: 'a named reasoning role with a distinct method' },
		{ kind: 'Flow', sigil: '→', gloss: 'a declared composition of other components' },
		{ kind: 'Skill', sigil: '◇', gloss: 'provides-only axiom — read, never wired into the DAG' }
	] as const;

	const entries = $derived(data.entries as CatalogEntry[]);
	const groups = $derived(
		KINDS.map((k) => ({ ...k, rows: entries.filter((e) => e.kind === k.kind) })).filter(
			(g) => g.rows.length
		)
	);

	const nsClass = (ns: string) =>
		ns.startsWith('slick.dev') ? 'ns-core' : ns.includes('anthropic.com') ? 'ns-vendor' : 'ns-gnx';
</script>

<svelte:head>
	<title>Catalog · gnx</title>
	<meta
		name="description"
		content="The gnx catalog — every registered component, its declared surface, and its maturity. Real manifests, read live."
	/>
</svelte:head>

<div class="doc catalog">
	<h1>The catalog</h1>
	<p class="lede">
		Every registered component, read live from its manifest. What you see is the declared
		surface — the same thing an agent reads when it composes. <b>{entries.length}</b>
		{entries.length === 1 ? 'component' : 'components'} registered today; the cix plugin family
		arrives as the catalog's first body of content.
	</p>

	{#each groups as g (g.kind)}
		<section class="kind-group">
			<h2><span class="sigil" aria-hidden="true">{g.sigil}</span> {g.kind}</h2>
			<p class="gloss">{g.gloss}</p>
			<table>
				<thead>
					<tr>
						<th>component</th>
						<th>provides</th>
						<th>namespace</th>
						<th>maturity</th>
					</tr>
				</thead>
				<tbody>
					{#each g.rows as e (e.type_url)}
						<tr class="k-{g.kind.toLowerCase()}">
							<td class="turl">
								<code>{e.type_url}</code>
								{#if e.description}<div class="desc">{e.description}</div>{/if}
								{#if e.relations.uses?.length}
									<div class="uses">uses: {e.relations.uses.join(', ')}</div>
								{/if}
							</td>
							<td class="tags">
								{#each e.provides as p (p)}<code class="tag">{p}</code>{/each}
							</td>
							<td><code class="ns {nsClass(e.namespace)}">{e.namespace}</code></td>
							<td class="mat mat-{e.maturity}">
								<span class="dot" aria-hidden="true">{e.maturity === 'shipped' ? '●' : '○'}</span>
								{e.maturity}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
	{/each}

	<section class="compositions">
		<h2>Compositions</h2>
		{#if data.composition}
			<!-- port-bearing components exist: the compiled batches render here (CatalogDag) -->
			<p>
				{data.composition.batches.length}
				{data.composition.batches.length === 1 ? 'stage' : 'stages'} compiled from declared ports.
				{#if data.composition.errors.length}
					<span class="err">{data.composition.errors.join(' · ')}</span>
				{/if}
			</p>
		{:else}
			<p class="empty">
				No wired compositions yet — every registered component carries discovery tags alone, and
				tags are searched, not wired. The first component to declare a port brings the pipeline
				view with it. To feel how port-graph composition works meanwhile, the
				<a href="{base}/docs/compose-components">composer</a> runs the same compiler on a live example.
			</p>
		{/if}
	</section>
</div>

<style>
	.catalog .lede {
		color: var(--muted);
		max-width: 44rem;
	}
	.kind-group {
		margin-top: 2.5rem;
	}
	.kind-group h2 {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin-bottom: 0.1rem;
	}
	.sigil {
		font-family: var(--font-mono, monospace);
		color: var(--accent);
	}
	.gloss {
		font-size: 0.8rem;
		color: var(--muted);
		margin: 0 0 0.9rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th {
		text-align: left;
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted);
		font-weight: 600;
		padding: 0.3rem 0.75rem 0.3rem 0;
		border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.25));
	}
	td {
		padding: 0.55rem 0.75rem 0.55rem 0;
		border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.12));
		vertical-align: top;
	}
	/* kind carries a structural mark, not just a label */
	tr.k-skill td:first-child {
		border-left: 1px dashed var(--muted);
		padding-left: 0.6rem;
	}
	tr.k-capability td:first-child {
		border-left: 3px solid var(--accent);
		padding-left: 0.6rem;
	}
	tr.k-agent td:first-child {
		border-left: 3px solid var(--resolved);
		padding-left: 0.6rem;
	}
	tr.k-flow td:first-child {
		border-left: 3px double var(--fg);
		padding-left: 0.6rem;
	}
	.turl code {
		font-weight: 600;
	}
	.desc {
		font-size: 0.78rem;
		color: var(--muted);
		margin-top: 0.25rem;
		max-width: 30rem;
	}
	.uses {
		font-size: 0.72rem;
		color: var(--muted);
		margin-top: 0.2rem;
		font-family: var(--font-mono, monospace);
	}
	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
	}
	.tag {
		font-size: 0.72rem;
		background: var(--panel);
		padding: 0.08rem 0.4rem;
		border-radius: 0.25rem;
		white-space: nowrap;
	}
	.ns {
		font-size: 0.72rem;
	}
	.ns-core {
		color: var(--resolved);
	}
	.ns-gnx {
		color: var(--accent);
	}
	.ns-vendor {
		color: var(--constraint);
	}
	.mat {
		white-space: nowrap;
		font-size: 0.78rem;
	}
	.mat .dot {
		font-size: 0.6rem;
	}
	.mat-shipped {
		color: var(--resolved);
	}
	.mat-designed {
		color: var(--muted);
	}
	.compositions {
		margin-top: 3rem;
	}
	.compositions .empty {
		color: var(--muted);
		max-width: 44rem;
	}
	.err {
		color: var(--constraint);
	}
	@media (max-width: 640px) {
		table,
		thead,
		tbody,
		tr,
		td {
			display: block;
		}
		thead {
			display: none;
		}
		td {
			border-bottom: none;
			padding: 0.15rem 0;
		}
		tr {
			border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.12));
			padding: 0.6rem 0;
		}
	}
</style>
