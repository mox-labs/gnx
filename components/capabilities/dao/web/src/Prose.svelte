<script lang="ts">
	// Text from a document. Every other component shows a document's words through this one,
	// so there is one place where Markdown becomes HTML.
	import { block, line } from './markdown';
	import { reader } from './reader.svelte';

	let { text, inline = false, shift = 0 }: { text: string; inline?: boolean; shift?: number } =
		$props();

	const r = reader();
	const html = $derived(inline ? line(text) : block(text, shift));

	function follow(event: MouseEvent): void {
		const link = (event.target as Element).closest('a[data-doc]');
		if (!link) return;
		event.preventDefault();
		r.showDocument(link.getAttribute('data-doc') ?? '');
	}
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
{#if inline}
	<span class="prose" onclick={follow}>{@html html}</span>
{:else}
	<div class="prose" onclick={follow}>{@html html}</div>
{/if}

<style>
	.prose {
		font-family: var(--dao-serif);
		overflow-wrap: break-word;
	}
	div.prose {
		max-width: 68ch;
	}
	.prose :global(:first-child) {
		margin-top: 0;
	}
	.prose :global(:last-child) {
		margin-bottom: 0;
	}
	.prose :global(p),
	.prose :global(ul),
	.prose :global(ol),
	.prose :global(blockquote),
	.prose :global(pre),
	.prose :global(.table) {
		margin: 0 0 0.85em;
	}
	.prose :global(h2),
	.prose :global(h3),
	.prose :global(h4),
	.prose :global(h5),
	.prose :global(h6) {
		font-family: var(--dao-sans);
		font-size: 0.95em;
		font-weight: 650;
		margin: 1.4em 0 0.4em;
		text-wrap: balance;
	}
	.prose :global(ul),
	.prose :global(ol) {
		padding-left: 1.3em;
	}
	.prose :global(li + li) {
		margin-top: 0.3em;
	}
	.prose :global(a) {
		color: var(--dao-accent);
		text-underline-offset: 0.15em;
	}
	.prose :global(a:focus-visible) {
		outline: 2px solid var(--dao-accent);
		outline-offset: 2px;
		border-radius: 2px;
	}
	.prose :global(code) {
		font-family: var(--dao-mono);
		font-size: 0.86em;
		background: var(--dao-sunk);
		padding: 0.1em 0.3em;
		border-radius: 3px;
	}
	.prose :global(pre) {
		background: var(--dao-sunk);
		padding: 0.8em 1em;
		border-radius: 6px;
		overflow-x: auto;
	}
	.prose :global(pre code) {
		background: none;
		padding: 0;
	}
	.prose :global(blockquote) {
		padding-left: 1em;
		border-left: 2px solid var(--dao-line);
		color: var(--dao-muted);
	}
	.prose :global(.table) {
		overflow-x: auto;
	}
	.prose :global(table) {
		border-collapse: collapse;
		font-family: var(--dao-sans);
		font-size: 0.88em;
		font-variant-numeric: tabular-nums;
	}
	.prose :global(th),
	.prose :global(td) {
		text-align: left;
		vertical-align: top;
		padding: 0.45em 0.9em 0.45em 0;
		border-bottom: 1px solid var(--dao-line);
	}
	.prose :global(th) {
		font-weight: 600;
		color: var(--dao-muted);
	}
	.prose :global(img) {
		max-width: 100%;
	}
	.prose :global(hr) {
		border: 0;
		border-top: 1px solid var(--dao-line);
		margin: 1.4em 0;
	}
</style>
