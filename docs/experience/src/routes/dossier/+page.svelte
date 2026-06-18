<script lang="ts">
	import Commentable from '$lib/components/Commentable.svelte';

	let { data } = $props();

	// The dossier proper (numbered Design docs) + the appendix (ground truth, order >= 90).
	const dossier = $derived(
		data.docs.filter(
			(d: { section: string; order: number }) => d.section === 'design' && d.order < 90
		)
	);
	const appendix = $derived(
		data.docs.filter(
			(d: { section: string; order: number }) => d.section === 'design' && d.order >= 90
		)
	);
</script>

<svelte:head>
	<title>gnx · genesis dossier</title>
</svelte:head>

<div class="cover">
	<header class="dossier-head">
		<a class="back" href="/">← gnx</a>
		<h1>The genesis dossier</h1>
		<p class="sub">the design surface behind gnx — built before the tool</p>
		<div class="kicker">internal · 2026-06</div>
	</header>

	<div class="cover-body doc">
		<Commentable doc="cover" blocks={data.cover.blocks} threads={data.threads} />
	</div>

	<aside class="gate-note">
		Every doc was ship-gated by an out-of-family evaluator against a written rubric. Read those
		first — the <a href="/docs/rubric">rubric</a> and the <a href="/evaluation">evaluation</a> are
		reviewable too. A verdict you disagree with means the rubric needs adjusting, not the doc.
	</aside>

	<h2 class="toc-label">The dossier</h2>
	<ol class="doclist">
		{#each dossier as doc (doc.slug)}
			<li>
				<span class="num">{String(doc.order).padStart(2, '0')}</span>
				<a href="/docs/{doc.slug}">{doc.title}</a>
				{#if data.counts.perDoc[doc.slug]}
					<span class="count">{data.counts.perDoc[doc.slug]} open</span>
				{/if}
			</li>
		{/each}
		{#each appendix as doc (doc.slug)}
			<li>
				<span class="num">A</span>
				<a href="/docs/{doc.slug}">{doc.title}</a>
				<span class="muted appendix-tag">appendix</span>
				{#if data.counts.perDoc[doc.slug]}
					<span class="count">{data.counts.perDoc[doc.slug]} open</span>
				{/if}
			</li>
		{/each}
	</ol>
</div>

<style>
	.dossier-head {
		margin-bottom: 2rem;
	}
	.dossier-head .back {
		font: 0.85rem var(--mono);
		color: var(--accent);
		text-decoration: none;
		display: inline-block;
		margin-bottom: 1.2rem;
	}
	.dossier-head h1 {
		font-family: var(--sans);
		font-size: 2rem;
		letter-spacing: -0.01em;
		margin: 0 0 0.4rem;
	}
	.dossier-head .sub {
		font: 0.95rem var(--mono);
		color: var(--muted);
		margin: 0 0 0.6rem;
	}
	.dossier-head .kicker {
		font: 0.72rem var(--mono);
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--muted);
	}
</style>
