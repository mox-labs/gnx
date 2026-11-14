<script lang="ts">
	// A participant or a position, named where a reader can pick it.
	import Mark from './Mark.svelte';
	import { participant, position, type Selection } from './model';
	import { reader } from './reader.svelte';

	let { kind, name }: Selection = $props();

	const r = reader();
	const known = $derived(
		kind === 'position' ? position(r.book, name) !== undefined : participant(r.book, name) !== undefined
	);
</script>

{#if known}
	<button
		type="button"
		class="name"
		aria-pressed={r.is({ kind, name })}
		onclick={() => r.select({ kind, name })}
		onpointerenter={() => (r.hot = { kind, name })}
		onpointerleave={() => (r.hot = null)}
	>
		{#if kind === 'participant'}<Mark kind={participant(r.book, name)?.kind ?? null} />{/if}{name}
	</button>
{:else}
	<span class="name unknown" title="Named in the documents, but not defined in them">{name}</span>
{/if}

<style>
	.name {
		display: inline-flex;
		align-items: center;
		gap: 0.4em;
		font: inherit;
		font-family: var(--dao-sans);
		font-size: 0.875rem;
		line-height: 1.3;
		color: var(--dao-ink);
		background: var(--dao-surface);
		border: 1px solid var(--dao-line);
		border-radius: 999px;
		padding: 0.2em 0.7em;
	}
	button.name {
		cursor: pointer;
	}
	button.name:hover {
		border-color: var(--dao-accent);
	}
	button.name:focus-visible {
		outline: 2px solid var(--dao-accent);
		outline-offset: 2px;
	}
	button.name[aria-pressed='true'] {
		border-color: var(--dao-accent);
		box-shadow: inset 0 0 0 1px var(--dao-accent);
	}
	.unknown {
		border-style: dashed;
		color: var(--dao-muted);
	}
</style>
