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
	<title>gnx: components that make agents more effective</title>
	<meta
		name="description"
		content="gnx: components that make agents more effective, and their work inspectable. Python packages installed with uv (hardline, matrix, ix, recon, dao) and skills installed into Claude Code as plugins."
	/>
</svelte:head>

<LandingComposition>
	{#snippet north()}<BrandHero
			tagline="components that make agents more effective, and their work inspectable"
		/>{/snippet}
	{#snippet west()}<Philosophy />{/snippet}
	{#snippet center()}<Sigil maxSize="clamp(180px, 30vh, 300px)" genesis />{/snippet}
	{#snippet east()}<NavigationCTA />{/snippet}
	{#snippet south()}<QuickStart />{/snippet}
</LandingComposition>
