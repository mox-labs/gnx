<script lang="ts">
	import type { Snippet } from 'svelte';

	// Cinematic quincunx (lifted from the cix landing): a full-viewport grid with
	// the sigil at center and content at the cardinal points, over an atmospheric
	// gradient. Collapses to vertical full-viewport slides on mobile.
	interface Props {
		north?: Snippet;
		west?: Snippet;
		center?: Snippet;
		east?: Snippet;
		south?: Snippet;
	}

	let { north, west, center, east, south }: Props = $props();
</script>

<div class="landing-grid">
	<div class="atmosphere" aria-hidden="true"></div>

	<header class="pos-north">{#if north}{@render north()}{/if}</header>
	<aside class="pos-west">{#if west}{@render west()}{/if}</aside>
	<main class="pos-center">{#if center}{@render center()}{/if}</main>
	<aside class="pos-east">{#if east}{@render east()}{/if}</aside>
	<nav class="pos-south">{#if south}{@render south()}{/if}</nav>
</div>

<style>
	.landing-grid {
		height: 100vh;
		height: 100dvh;
		position: relative;
		overflow: hidden;
		display: grid;
		grid-template-columns: minmax(min-content, 1fr) minmax(200px, 1.1fr) minmax(min-content, 1fr);
		grid-template-rows: auto 1fr auto;
		grid-template-areas:
			'.    north  .'
			'west center east'
			'.    south  .';
		gap: var(--space-2);
		padding: var(--space-4);
	}

	.atmosphere {
		position: absolute;
		inset: 0;
		pointer-events: none;
		z-index: 0;
		background:
			radial-gradient(ellipse 60% 50% at 50% 22%, var(--spark-atmosphere) 0%, transparent 64%),
			radial-gradient(ellipse 80% 60% at 50% 108%, var(--emergence-atmosphere) 0%, transparent 55%);
	}

	.pos-north {
		grid-area: north;
		z-index: 1;
		align-self: end;
		display: flex;
		justify-content: center;
	}
	.pos-west {
		grid-area: west;
		z-index: 1;
		display: flex;
		align-items: center;
		justify-content: flex-start;
	}
	.pos-center {
		grid-area: center;
		z-index: 1;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.pos-east {
		grid-area: east;
		z-index: 1;
		display: flex;
		align-items: center;
		justify-content: flex-end;
	}
	.pos-south {
		grid-area: south;
		z-index: 1;
		align-self: start;
		display: flex;
		justify-content: center;
	}

	@media (max-width: 1024px) {
		.landing-grid {
			grid-template-columns: 1fr 1.6fr 1fr;
		}
	}

	/* Mobile: vertical scrollytelling — each position a full-viewport slide */
	@media (max-width: 768px) {
		.landing-grid {
			display: flex;
			flex-direction: column;
			height: auto;
			overflow: visible;
			gap: 0;
			padding: 0;
		}
		.pos-north { order: 1; }
		.pos-center { order: 2; }
		.pos-west { order: 3; }
		.pos-east { order: 4; }
		.pos-south { order: 5; }
		.pos-north,
		.pos-center,
		.pos-west,
		.pos-east,
		.pos-south {
			min-height: 100vh;
			min-height: 100dvh;
			display: flex;
			flex-direction: column;
			align-items: center;
			justify-content: center;
			text-align: center;
			padding: var(--space-5);
		}
		.pos-south {
			min-height: 60vh;
			min-height: 60dvh;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.atmosphere {
			background: none;
		}
	}
</style>
