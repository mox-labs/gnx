<script lang="ts">
	import { STATUS_STYLE, type InterpretantStatus, type ConvergenceSurface } from '$lib/dossier/types';
	import { conceptSlug } from '$lib/dossier/slug';
	import Commentable from '$lib/components/Commentable.svelte';

	let { data } = $props();

	// in-page drill: atlas (no region) → terrain (a region). The concept is a routed folio (annotatable).
	// Region defaults to the URL (?region=, e.g. a folio breadcrumb); a local click overrides it.
	let picked = $state<string | null | undefined>(undefined);
	const openGroupId = $derived(picked === undefined ? (data.region ?? null) : picked);

	const s = $derived(data.surface as ConvergenceSurface);
	const openGroup = $derived(openGroupId ? s.groups.find((g) => g.id === openGroupId) : null);

	const DOT: Record<InterpretantStatus, string> = {
		settled: '●',
		converged: '●',
		contested: '◑',
		open: '○'
	};
	const conceptsIn = (gid: string) => s.concepts.filter((c) => c.group === gid);
	const owedIn = (gid: string) => conceptsIn(gid).reduce((n, c) => n + c.tensions.length, 0);
	const commentsIn = (gid: string) =>
		conceptsIn(gid).reduce((n, c) => n + (data.conceptCounts[c.id] ?? 0), 0);
	const oneLine = (c: { convergedInterpretant?: string; body?: string }) =>
		(c.convergedInterpretant || c.body || '').split(/(?<=\.)\s/)[0];
</script>

<svelte:head>
	<title>gnx · the dossier</title>
</svelte:head>

<div class="dossier">
	{#if !openGroup}
		<!-- THE SUMMIT — abstract + the map of regions -->
		<div class="kicker">{s.meta.generatedFrom}</div>
		<h1>The gnx dossier</h1>
		<div class="doc summit">
			<Commentable doc={data.summit.doc} blocks={data.summit.blocks} threads={data.summit.threads} />
		</div>

		<div class="maplabel">The map — {s.groups.length} regions</div>
		<ol class="atlas">
			{#each s.groups as g, i (g.id)}
				{@const cs = conceptsIn(g.id)}
				{@const owed = owedIn(g.id)}
				{@const comments = commentsIn(g.id)}
				<li>
					<button class="region" onclick={() => (picked = g.id)}>
						<span class="t">{i + 1} · {g.title}</span>
						<span class="dots" aria-hidden="true">
							{#each cs as c (c.id)}
								<span style="color:{STATUS_STYLE[c.status].color}">{DOT[c.status]}</span>
							{/each}
						</span>
						<span class="meta">
							{cs.length} · {#if owed}<b class="owed">{owed} owed</b>{:else}settled{/if}
							{#if comments}<span class="comments">{comments} ✎</span>{/if}
						</span>
					</button>
				</li>
			{/each}
		</ol>
	{:else}
		<!-- THE TERRAIN — a region; its concepts are POIs that open as annotatable folios -->
		<nav class="crumbs">
			<button class="crumb" onclick={() => (picked = null)}>Summit</button>
			<span class="sep">/</span>
			<span class="crumb here">{openGroup.title}</span>
		</nav>
		<header class="region-head">
			<h2>{openGroup.title}</h2>
			<p class="blurb">{openGroup.blurb}</p>
		</header>
		<ol class="pois">
			{#each conceptsIn(openGroup.id) as c (c.id)}
				{@const cst = STATUS_STYLE[c.status]}
				<li>
					<a class="poi" style="border-left-color:{cst.color}" href="/dossier/{conceptSlug(c.id)}">
						<div class="head">
							<span class="term">{c.term}</span>
							{#if c.layer}<span class="layer">{c.layer}</span>{/if}
							<span class="badge" style="color:{cst.color};background:{cst.bg}">{cst.label}</span>
						</div>
						<div class="line">{oneLine(c)}</div>
						<div class="foot">
							{#if c.tensions.length}
								<span class="owed">{c.tensions.length} decision{c.tensions.length === 1 ? '' : 's'} owed</span>
							{/if}
							{#if data.conceptCounts[c.id]}
								<span class="comments">{data.conceptCounts[c.id]} open ✎</span>
							{/if}
						</div>
					</a>
				</li>
			{/each}
		</ol>
	{/if}
</div>

<style>
	.dossier {
		max-width: 54rem;
		margin: 0 auto;
		padding: 2rem 1.5rem 7rem;
		font-family: var(--mono);
		color: var(--fg);
	}

	/* --- summit --- */
	.kicker {
		font-size: 0.72rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--muted);
	}
	h1 {
		font-family: var(--sans);
		font-size: 2.2rem;
		letter-spacing: -0.02em;
		margin: 0.3rem 0 1rem;
	}
	.maplabel {
		font: 600 0.7rem var(--sans);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
		margin: 2rem 0 0.8rem;
	}
	ol.atlas {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	button.region {
		width: 100%;
		display: flex;
		align-items: center;
		gap: 0.8rem;
		text-align: left;
		font-family: var(--mono);
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 0.85rem 1rem;
		cursor: pointer;
		color: var(--fg);
		transition:
			border-color 0.12s,
			transform 0.06s;
	}
	button.region:hover {
		border-color: var(--accent);
	}
	button.region:active {
		transform: translateY(1px);
	}
	.region .t {
		font-size: 1rem;
		flex: 1;
	}
	.region .dots {
		letter-spacing: 0.12em;
		font-size: 0.85rem;
	}
	.region .meta {
		font-size: 0.75rem;
		color: var(--muted);
		min-width: 7rem;
		text-align: right;
		display: flex;
		gap: 0.5rem;
		justify-content: flex-end;
	}
	.owed {
		color: var(--ci-red);
	}
	.comments {
		color: var(--accent);
	}

	/* --- terrain --- */
	.crumbs {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8rem;
		border-bottom: 1px solid var(--line);
		padding-bottom: 0.7rem;
		margin-bottom: 1.4rem;
		flex-wrap: wrap;
	}
	.crumb {
		font-family: var(--mono);
		background: none;
		border: none;
		color: var(--muted);
		cursor: pointer;
		padding: 0.1rem 0.15rem;
		font-size: 0.8rem;
	}
	.crumb:hover {
		color: var(--accent);
	}
	.crumb.here {
		color: var(--fg);
		cursor: default;
	}
	.sep {
		color: var(--line);
	}
	.region-head h2 {
		font-family: var(--sans);
		font-size: 1.6rem;
		letter-spacing: -0.01em;
		margin: 0 0 0.3rem;
	}
	.region-head .blurb {
		font-size: 0.9rem;
		color: var(--muted);
		line-height: 1.6;
		margin: 0 0 1.4rem;
		max-width: 46rem;
	}
	ol.pois {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	a.poi {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		text-decoration: none;
		font-family: var(--mono);
		background: var(--panel);
		border: 1px solid var(--line);
		border-left-width: 3px;
		border-radius: 7px;
		padding: 0.75rem 0.9rem;
		color: var(--fg);
		transition: border-color 0.12s;
	}
	a.poi:hover {
		border-color: var(--accent);
	}
	.poi .head {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
	}
	.term {
		font-size: 1rem;
	}
	.layer {
		font-size: 0.64rem;
		color: var(--muted);
	}
	.badge {
		margin-left: auto;
		font-size: 0.6rem;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		padding: 0.05rem 0.4rem;
		border-radius: 0.3rem;
		font-weight: 600;
	}
	.line {
		font-size: 0.82rem;
		line-height: 1.5;
		color: var(--muted);
	}
	.poi .foot {
		display: flex;
		gap: 0.8rem;
		font-size: 0.68rem;
	}
</style>
