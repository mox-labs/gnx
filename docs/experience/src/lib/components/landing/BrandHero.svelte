<script lang="ts">
	interface Props {
		brand?: string;
		tagline?: string;
	}
	let { brand = 'gnx', tagline = 'an extensions marketplace for agents' }: Props = $props();
</script>

<div class="brand-hero">
	<h1 class="brand">{brand}</h1>
	<p class="tagline">{tagline}</p>
</div>

<style>
	.brand-hero {
		text-align: center;
	}
	.brand {
		font-family: var(--font-sans);
		font-size: clamp(3rem, 7vw, 5rem);
		font-weight: 700;
		letter-spacing: var(--tracking-tight);
		line-height: 1;
		margin: 0;
		color: var(--spark-core);
		text-shadow:
			0 0 4px oklch(75% 0.18 240),
			0 0 16px oklch(62% 0.15 240 / 0.55),
			0 0 44px oklch(55% 0.12 240 / 0.3),
			0 0 90px oklch(50% 0.08 240 / 0.12);
	}
	.brand::after {
		content: '.';
		color: var(--ci-green);
		text-shadow: 0 0 16px oklch(70% 0.18 145 / 0.5);
	}
	.tagline {
		font-family: var(--font-mono);
		font-size: var(--type-base);
		color: var(--dao-muted);
		letter-spacing: var(--tracking-normal);
		margin: var(--space-2) 0 0;
	}
	@media (prefers-reduced-motion: reduce) {
		.brand {
			text-shadow: none;
		}
	}
</style>
