<script lang="ts">
	// The gnx sigil. Three trichrome layers, in genesis order:
	//   red heptagon + heptagram — the MACHINE (the substrate, assembled first);
	//   blue spark — the HUMAN (the ignition at the center);
	//   green triskelion, straight arms — EMERGENCE through extensions.
	// `genesis` plays the making-of once (machine assembles → spark ignites →
	// arms extend); without it — and under prefers-reduced-motion — the mark is static.
	interface Props {
		maxSize?: string;
		genesis?: boolean;
	}
	let { maxSize = '84px', genesis = false }: Props = $props();
</script>

<div class="sigil-mark" class:genesis style="--max-size: {maxSize}">
	<svg viewBox="0 0 200 200" class="mark" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
		<!-- The machine — red heptagon + inner heptagram -->
		<path class="heptagon draw d-hept" pathLength="1" d="M 100 40 L 146.91 62.59 L 158.49 113.35 L 126.03 154.05 L 73.97 154.05 L 41.51 113.35 L 53.09 62.59 Z" fill="none" stroke-width="3.5" stroke-linejoin="round" opacity="0.9" />
		<path class="heptagon draw d-gram" pathLength="1" d="M 100 40 L 158.49 113.35 L 73.97 154.05 L 53.09 62.59 L 146.91 62.59 L 126.03 154.05 L 41.51 113.35 Z" fill="none" stroke-width="1.6" stroke-linejoin="round" opacity="0.55" />

		<!-- The human — blue spark: core ignites, rays reach the ring -->
		<g class="ignite">
			<circle class="spark-halo" cx="100" cy="100" r="26" />
			<circle class="spark-fill" cx="100" cy="100" r="8.75" />
		</g>
		<g class="spark" stroke-width="5.5" stroke-linecap="round">
			<line class="draw d-ray1" pathLength="1" x1="100" y1="100" x2="100" y2="65.6" />
			<line class="draw d-ray2" pathLength="1" x1="100" y1="100" x2="129.8" y2="117.2" />
			<line class="draw d-ray3" pathLength="1" x1="100" y1="100" x2="70.2" y2="117.2" />
		</g>

		<!-- Emergence — green triskelion: the ring, then three straight arms extend -->
		<g class="emergence" fill="none" stroke-linecap="round" stroke-linejoin="round">
			<circle class="draw d-ring" pathLength="1" cx="100" cy="100" r="34.4" stroke-width="4" />
			<path class="draw d-arm1" pathLength="1" d="M 100 65.6 L 100 22" stroke-width="4.5" transform="rotate(0 100 100)" />
			<path class="draw d-arm2" pathLength="1" d="M 100 65.6 L 100 22" stroke-width="4.5" transform="rotate(120 100 100)" />
			<path class="draw d-arm3" pathLength="1" d="M 100 65.6 L 100 22" stroke-width="4.5" transform="rotate(240 100 100)" />
		</g>
	</svg>
</div>

<style>
	.sigil-mark {
		display: inline-flex;
		align-items: center;
		justify-content: center;
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
	.spark-halo {
		fill: oklch(80% 0.22 245);
		opacity: 0;
	}

	/* ——— genesis: the making-of, played once ———
	   Beat 1 · 0.0–1.6s  the machine assembles (heptagon draws, heptagram threads)
	   Beat 2 · 1.8–2.7s  the human ignites (core flash + halo, rays extend)
	   Beat 3 · 2.7–4.1s  emergence through extensions (ring draws, arms grow outward) */
	.genesis .draw {
		stroke-dasharray: 1;
		stroke-dashoffset: 1;
		animation: sigil-draw 0.8s cubic-bezier(0.4, 0, 0.2, 1) both;
	}
	.genesis .d-hept  { animation-duration: 1.1s; animation-delay: 0.15s; }
	.genesis .d-gram  { animation-duration: 0.9s; animation-delay: 0.75s; }
	.genesis .d-ray1  { animation-duration: 0.45s; animation-delay: 2.25s; }
	.genesis .d-ray2  { animation-duration: 0.45s; animation-delay: 2.35s; }
	.genesis .d-ray3  { animation-duration: 0.45s; animation-delay: 2.45s; }
	.genesis .d-ring  { animation-duration: 0.8s;  animation-delay: 2.7s; }
	.genesis .d-arm1  { animation-duration: 0.55s; animation-delay: 3.3s; }
	.genesis .d-arm2  { animation-duration: 0.55s; animation-delay: 3.45s; }
	.genesis .d-arm3  { animation-duration: 0.55s; animation-delay: 3.6s; }

	.genesis .ignite {
		transform-box: fill-box;
		transform-origin: center;
		transform: scale(0);
		animation: sigil-ignite 0.7s cubic-bezier(0.2, 0.9, 0.3, 1.2) 1.8s both;
	}
	.genesis .spark-halo {
		transform-box: fill-box;
		transform-origin: center;
		animation: sigil-halo 1s ease-out 1.85s both;
	}

	@keyframes sigil-draw {
		to { stroke-dashoffset: 0; }
	}
	@keyframes sigil-ignite {
		0% { transform: scale(0); }
		60% { transform: scale(1.3); }
		100% { transform: scale(1); }
	}
	@keyframes sigil-halo {
		0% { opacity: 0; transform: scale(0.2); }
		35% { opacity: 0.45; }
		100% { opacity: 0; transform: scale(1.6); }
	}

	@media (prefers-reduced-motion: reduce) {
		.genesis .draw,
		.genesis .ignite,
		.genesis .spark-halo {
			animation: none;
		}
		.genesis .draw {
			stroke-dashoffset: 0;
		}
		.genesis .ignite {
			transform: scale(1);
		}
	}
</style>
