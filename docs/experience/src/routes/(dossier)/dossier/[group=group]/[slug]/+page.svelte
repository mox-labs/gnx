<script lang="ts">
	import Commentable from '$lib/components/Commentable.svelte';
	import { groupById } from '$lib/registers';

	let { data } = $props();

	const label = $derived(groupById(data.group)?.label ?? data.group);
</script>

<svelte:head>
	<title>{data.doc.title} · gnx dossier</title>
</svelte:head>

<article class="doc">
	<nav class="crumb">
		<a href="/dossier">Summit</a>
		<span class="sep">/</span>
		<span class="here">{label}</span>
	</nav>

	<Commentable doc={data.doc.slug} blocks={data.doc.blocks} threads={data.threads} />
</article>

<style>
	.crumb {
		display: flex;
		gap: 0.5rem;
		align-items: baseline;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		margin-bottom: 1.5rem;
	}
	.crumb a {
		color: var(--muted);
		text-decoration: none;
	}
	.crumb a:hover {
		color: var(--fg);
	}
	.crumb .sep {
		opacity: 0.35;
	}
	.crumb .here {
		opacity: 0.6;
		font-weight: 600;
	}
</style>
