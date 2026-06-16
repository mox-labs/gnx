<script lang="ts">
	// The gnx sigil — static (no animation). Three trichrome layers:
	// red constraint (heptagon + inner heptagram), green emergence (a triskelion,
	// three arms), blue spark (three rays + core).
	interface Props {
		maxSize?: string;
	}
	let { maxSize = '84px' }: Props = $props();
</script>

<div class="sigil-mark" style="--max-size: {maxSize}">
	<svg viewBox="0 0 200 200" class="mark" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
		<!-- Constraint — red heptagon + inner heptagram -->
		<path class="heptagon" d="M 100 40 L 146.91 62.59 L 158.49 113.35 L 126.03 154.05 L 73.97 154.05 L 41.51 113.35 L 53.09 62.59 Z" fill="none" stroke-width="3.5" stroke-linejoin="round" opacity="0.9" />
		<path class="heptagon" d="M 100 40 L 158.49 113.35 L 73.97 154.05 L 53.09 62.59 L 146.91 62.59 L 126.03 154.05 L 41.51 113.35 Z" fill="none" stroke-width="1.6" stroke-linejoin="round" opacity="0.55" />

		<!-- Emergence — green triskelion (three arms) -->
		<g class="emergence" fill="none" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
			<path d="M 100 100 L 100 60 Q 100 50 113 54" transform="rotate(60 100 100)" />
			<path d="M 100 100 L 100 60 Q 100 50 113 54" transform="rotate(180 100 100)" />
			<path d="M 100 100 L 100 60 Q 100 50 113 54" transform="rotate(300 100 100)" />
		</g>

		<!-- Spark — blue rays + core -->
		<g class="spark" stroke-width="5.5" stroke-linecap="round">
			<line x1="100" y1="100" x2="100" y2="65.6" />
			<line x1="100" y1="100" x2="129.8" y2="117.2" />
			<line x1="100" y1="100" x2="70.2" y2="117.2" />
		</g>
		<circle class="spark-fill" cx="100" cy="100" r="8.75" />
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
