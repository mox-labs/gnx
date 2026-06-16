<script lang="ts">
	// The gnx sigil — lifted from the cix landing mark (gnx = the cix sigil).
	// Three trichrome layers: red heptagon (Constraint, counter-rotating),
	// blue spark (Spark, three rays), green circle + stem (Emergence).
	import { onMount } from 'svelte';

	interface Props {
		maxSize?: string;
	}
	let { maxSize = '84px' }: Props = $props();

	let time = $state(0);
	let reducedMotion = $state(false);

	onMount(() => {
		const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
		reducedMotion = mq.matches;
		if (reducedMotion) return;
		let animId: number;
		let last = performance.now();
		const animate = (now: number) => {
			time += now - last;
			last = now;
			animId = requestAnimationFrame(animate);
		};
		animId = requestAnimationFrame(animate);
		return () => cancelAnimationFrame(animId);
	});

	// Geometry — heptagon radius 60 in a 200×200 viewBox.
	const heptR = 60;
	const starApothem = heptR * Math.cos((2 * Math.PI) / 7);
	const treyaR = starApothem - 3;
	const armEnd = 50 * (treyaR / 19);

	let sparkRotation = $derived((time * 0.01) % 360);
	let emergenceRotation = $derived((time * 0.005) % 360);
	let heptagonRotation = $derived(-(time * 0.0072) % 360);

	const sparkRays = [
		{ x2: 100, y2: 100 - treyaR },
		{ x2: 100 + treyaR * Math.cos((30 * Math.PI) / 180), y2: 100 + treyaR * Math.sin((30 * Math.PI) / 180) },
		{ x2: 100 - treyaR * Math.cos((30 * Math.PI) / 180), y2: 100 + treyaR * Math.sin((30 * Math.PI) / 180) }
	];
</script>

<div class="sigil-mark" style="--max-size: {maxSize}">
	<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" class="mark" aria-hidden="true">
		<g transform="rotate({emergenceRotation}, 100, 100)">
			<circle cx="100" cy="100" r={treyaR} fill="none" stroke-width="4" class="emergence" />
			<line x1="100" y1={100 - treyaR} x2="100" y2={100 - armEnd} stroke-width="4" stroke-linecap="round" class="emergence" />
		</g>
		<g transform="rotate({heptagonRotation}, 100, 100)">
			<path d="M 100.00 40.00 L 146.91 62.59 L 158.49 113.35 L 126.03 154.05 L 73.97 154.05 L 41.51 113.35 L 53.09 62.59 Z" fill="none" stroke-width="3.50" stroke-linejoin="round" opacity="0.9" class="heptagon" />
			<path d="M 100.00 40.00 L 158.49 113.35 L 73.97 154.05 L 53.09 62.59 L 146.91 62.59 L 126.03 154.05 L 41.51 113.35 Z" fill="none" stroke-width="1.05" stroke-linejoin="round" opacity="0.2" class="heptagon" />
		</g>
		<g transform="rotate({sparkRotation}, 100, 100)">
			{#each sparkRays as ray (ray.x2)}
				<line x1="100" y1="100" x2={ray.x2} y2={ray.y2} stroke-width="5.5" stroke-linecap="round" class="spark" />
			{/each}
			<circle cx="100" cy="100" r="8.75" class="spark-fill" />
		</g>
	</svg>
</div>

<style>
	.sigil-mark {
		display: inline-flex;
	}
	.mark {
		width: var(--max-size);
		height: var(--max-size);
		display: block;
	}
	.heptagon {
		stroke: var(--ci-red);
	}
	.emergence {
		stroke: var(--emergence-core);
	}
	.spark {
		stroke: oklch(80% 0.22 245);
	}
	.spark-fill {
		fill: oklch(80% 0.22 245);
	}
</style>
