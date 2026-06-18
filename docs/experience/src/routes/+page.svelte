<script lang="ts">
	import Sigil from '$lib/components/Sigil.svelte';
	import {
		LandingComposition,
		BrandHero,
		Philosophy,
		NavigationCTA,
		QuickStart
	} from '$lib/components/landing';
</script>

<svelte:head>
	<title>gnx — an extensions marketplace for agents</title>
	<meta
		name="description"
		content="A component registry, Claude Code marketplace, and CLI of composable cognitive extensions. Your agent composes capability from the catalog; you govern what it composes from."
	/>
</svelte:head>

<LandingComposition>
	{#snippet north()}<BrandHero />{/snippet}
	{#snippet west()}<Philosophy />{/snippet}
	{#snippet center()}<Sigil maxSize="clamp(180px, 30vh, 300px)" />{/snippet}
	{#snippet east()}<NavigationCTA />{/snippet}
	{#snippet south()}<QuickStart />{/snippet}
</LandingComposition>
