<script lang="ts">
	import type { CatalogEntry, PluginEntry } from './+page.server';
	import { base } from '$app/paths';

	let { data } = $props();

	// Kind is a type-level constraint, not a color. Shape-primary sigils; Skill last
	// because it's axiomatic, not pipeline. (tufte, catalog-viz-design)
	const KINDS = [
		{ kind: 'Capability', sigil: '□', gloss: 'runnable — a Python package reached over a transport' },
		{ kind: 'Agent', sigil: '○', gloss: 'a named reasoning role with a distinct method' },
		{ kind: 'Flow', sigil: '→', gloss: 'a declared composition of other components' },
		{ kind: 'Skill', sigil: '◇', gloss: 'a practice, read into context when its trigger matches' }
	] as const;

	const entries = $derived(data.entries as CatalogEntry[]);
	const plugins = $derived(data.plugins as PluginEntry[]);
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
		content="Every component in the gnx marketplace: eleven installable plugins cut from the component inventory, read live from disk."
	/>
</svelte:head>

<div class="doc catalog">
	<h1>The catalog</h1>
	<p class="lede">
		gnx is a marketplace of composable components for Claude Code. <b>{plugins.length}</b> plugins
		are installable today, cut from <b>{entries.length}</b> components read live from this
		repository. Generation 0 — the composition layer, where components declare ports and a
		composer wires them — is upcoming; what is here now is the catalog those compositions will
		draw from.
	</p>

	<section class="install">
		<h2>Install</h2>
		<p class="gloss">Add the marketplace once, then install what you want.</p>
		<pre><code>/plugin marketplace add moxlabs/gnx
/plugin install {plugins[0]?.name ?? 'guild-arch'}@gnx</code></pre>

		<table>
			<thead>
				<tr>
					<th>plugin</th>
					<th>what it does</th>
					<th class="num">agents</th>
					<th class="num">skills</th>
					<th>ver</th>
				</tr>
			</thead>
			<tbody>
				{#each plugins as p (p.name)}
					<tr>
						<td class="pname"><code>{p.name}</code><div class="cat">{p.category}</div></td>
						<td class="desc-cell">{p.description}</td>
						<td class="num">{p.agents || '—'}</td>
						<td class="num">{p.skills || '—'}</td>
						<td><code class="ver">{p.version}</code></td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<h2 class="inventory-head">The component inventory</h2>
	<p class="gloss inventory-gloss">
		A plugin is a bundle; a component is the unit. One component may ship in several plugins —
		<code>trust-boundaries</code> is in two — because bundling is a projection decision, separate
		from authoring.
	</p>

	{#each groups as g (g.kind)}
		<section class="kind-group">
			<h3><span class="sigil" aria-hidden="true">{g.sigil}</span> {g.kind} <span class="count">{g.rows.length}</span></h3>
			<p class="gloss">{g.gloss}</p>
			<table>
				<thead>
					<tr>
						<th>component</th>
						<th>ships in</th>
						<th>identity</th>
					</tr>
				</thead>
				<tbody>
					{#each g.rows as e (e.slug)}
						<tr class="k-{g.kind.toLowerCase()}">
							<td class="turl">
								<code>{e.slug}</code>
								{#if e.description}<div class="desc">{e.description}</div>{/if}
								{#if e.relations.uses?.length}
									<div class="uses">uses: {e.relations.uses.join(', ')}</div>
								{/if}
							</td>
							<td class="tags">
								{#each e.plugins as p (p)}<code class="tag">{p}</code>{/each}
								{#if !e.plugins.length}<span class="unbundled">not bundled</span>{/if}
							</td>
							<td>
								{#if e.type_url}
									<code class="ns {nsClass(e.namespace ?? '')}">{e.type_url}</code>
								{:else}
									<span class="pending" title="Manifest v1 is being settled in slick — see below">—</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
	{/each}

	<section class="compositions">
		<h2>Identity and composition</h2>
		<p>
			<b>{data.manifested}</b> of {entries.length} components carry a manifest — the declared
			<code>type_url</code>, <code>provides</code> and <code>requires</code> that give a component
			a name a composer can resolve.
		</p>
		<p class="note">
			That number is small on purpose. The Manifest v1 shape is still being settled, and minting
			seventy manifests against a moving spec would mean rewriting seventy. The three that exist
			are the pilot that tests the grammar; the rest follow once the shape stops moving. Until
			then a component is installable but not yet composable, and the catalog says so rather than
			showing an empty column as though it were a defect.
		</p>
		{#if data.composition}
			<p>
				{data.composition.batches.length}
				{data.composition.batches.length === 1 ? 'stage' : 'stages'} compiled from declared ports.
				{#if data.composition.errors.length}
					<span class="err">{data.composition.errors.join(' · ')}</span>
				{/if}
			</p>
		{:else}
			<p class="empty">
				No wired compositions yet — the manifested components carry discovery tags alone, and tags
				are searched, not wired. The first component to declare a port brings the pipeline view
				with it. To feel how port-graph composition works meanwhile, the
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
	.install {
		margin-top: 2.5rem;
	}
	.install pre {
		background: var(--panel);
		padding: 0.75rem 1rem;
		border-radius: 0.3rem;
		overflow-x: auto;
		font-size: 0.82rem;
		margin: 0 0 1.25rem;
	}
	.inventory-head {
		margin-top: 3rem;
	}
	.inventory-gloss {
		max-width: 44rem;
		margin-bottom: 0;
	}
	.kind-group {
		margin-top: 2rem;
	}
	.kind-group h3 {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin-bottom: 0.1rem;
	}
	.count {
		font-size: 0.72rem;
		color: var(--muted);
		font-weight: 400;
		font-variant-numeric: tabular-nums;
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
	th.num,
	td.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
		width: 4rem;
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
	.turl code,
	.pname code {
		font-weight: 600;
	}
	.cat,
	.desc {
		font-size: 0.78rem;
		color: var(--muted);
		margin-top: 0.25rem;
		max-width: 30rem;
	}
	.desc-cell {
		color: var(--muted);
		max-width: 32rem;
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
	.unbundled,
	.pending {
		font-size: 0.72rem;
		color: var(--muted);
	}
	.ns,
	.ver {
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
	.compositions {
		margin-top: 3rem;
	}
	.compositions .empty,
	.compositions .note {
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
		th.num,
		td.num {
			text-align: left;
			width: auto;
		}
		tr {
			border-bottom: 1px solid var(--line, rgba(128, 128, 128, 0.12));
			padding: 0.6rem 0;
		}
	}
</style>
