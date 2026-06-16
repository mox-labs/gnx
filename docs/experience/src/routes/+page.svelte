<script lang="ts">
	import Commentable from '$lib/components/Commentable.svelte';
	import Sigil from '$lib/components/Sigil.svelte';

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
	// "Start reading" points at the public front door.
	const first = $derived(data.docs.find((d: { section: string }) => d.section === 'start'));
</script>

<svelte:head>
	<title>gnx · genesis dossier</title>
</svelte:head>

<div class="cover">
	<header class="hero">
		<div class="atmosphere" aria-hidden="true"></div>
		<Sigil maxSize="clamp(200px, 34vh, 320px)" />
		<h1>gnx</h1>
		<p class="sub">an extensions marketplace for agents</p>
		{#if first}
			<a class="enter" href="/docs/{first.slug}">Enter the dossier → {first.title}</a>
		{/if}
		<div class="kicker">genesis dossier · internal · 2026-06</div>
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
