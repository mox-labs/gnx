<script lang="ts">
	// The sign for what a participant is. Shape carries it as well as colour: a person is a
	// disc, an agent a square, and one the documents do not classify is an open ring.
	import type { Kind } from './model';

	let { kind }: { kind: Kind } = $props();
	const label = $derived(kind === 'human' ? 'person' : kind === 'agent' ? 'agent' : 'kind not stated');
</script>

<span class="mark {kind ?? 'unstated'}" role="img" aria-label={label}></span>

<style>
	.mark {
		flex: none;
		display: inline-block;
		width: 0.62em;
		height: 0.62em;
		box-sizing: border-box;
	}
	.human {
		border-radius: 50%;
		background: var(--dao-human);
	}
	.agent {
		border-radius: 1.5px;
		background: var(--dao-agent);
	}
	.unstated {
		border-radius: 50%;
		border: 1.5px dashed var(--dao-muted);
	}
</style>
