<script lang="ts">
	import Block from '$lib/components/Block.svelte';
	import Composer from '$lib/components/Composer.svelte';
	import DecisionCard from '$lib/components/DecisionCard.svelte';
	import DecisionMatrix from '$lib/components/DecisionMatrix.svelte';
	import { offsetsOfRange, quoteAt } from '$lib/client/anchor';
	import type { DocBlock } from '$lib/server/content';
	import type { Thread } from '$lib/server/feedback';

	let {
		doc,
		blocks,
		threads = [],
		reviewMode = false
	}: { doc: string; blocks: DocBlock[]; threads?: Thread[]; reviewMode?: boolean } = $props();

	interface PendingSelection {
		bid: string;
		quote: string;
		prefix: string;
		suffix: string;
	}

	let pending = $state<PendingSelection | null>(null);
	let menu = $state<{ x: number; y: number; sel: PendingSelection } | null>(null);
	let container: HTMLElement | undefined = $state();

	const threadsByBlock = $derived.by(() => {
		const map = new Map<string, Thread[]>();
		for (const t of threads) {
			const list = map.get(t.bid) ?? [];
			list.push(t);
			map.set(t.bid, list);
		}
		return map;
	});

	function captureSelection() {
		const sel = window.getSelection();
		menu = null;
		if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
		const range = sel.getRangeAt(0);
		const node =
			range.commonAncestorContainer instanceof Element
				? range.commonAncestorContainer
				: range.commonAncestorContainer.parentElement;
		const blockEl = node?.closest('.block');
		const contentEl = node?.closest('.content') as HTMLElement | null;
		if (!blockEl || !contentEl || !container?.contains(blockEl)) return;

		const offsets = offsetsOfRange(contentEl, range);
		if (!offsets) return;
		const { quote, prefix, suffix } = quoteAt(contentEl, offsets.start, offsets.end);
		if (!quote.trim() || quote.length > 500) return;

		const rect = range.getBoundingClientRect();
		menu = {
			x: rect.left + rect.width / 2,
			y: rect.top,
			sel: { bid: blockEl.id, quote, prefix, suffix }
		};
	}

	function startComment() {
		if (!menu) return;
		pending = menu.sel;
		menu = null;
		window.getSelection()?.removeAllRanges();
		document.getElementById(pending.bid)?.scrollIntoView({ block: 'nearest' });
	}
</script>

<svelte:document
	onpointerup={captureSelection}
	onpointerdown={(e) => {
		if (menu && !(e.target as Element).closest('.selmenu')) menu = null;
	}}
/>

{#if menu}
	<button class="selmenu" style="left: {menu.x}px; top: {menu.y}px" onclick={startComment}>
		comment
	</button>
{/if}

<div bind:this={container}>
	{#each blocks as block, i (block.id + i)}
		{#if block.variant === 'composer'}
			<Composer />
		{:else if block.variant === 'decision'}
			<DecisionCard {doc} data={block.data as never} threads={threadsByBlock.get(block.id) ?? []} />
		{:else if block.variant === 'decision-matrix'}
			<DecisionMatrix data={block.data as never} />
		{:else}
			<Block
				{doc}
				{block}
				{reviewMode}
				threads={threadsByBlock.get(block.id) ?? []}
				pending={pending?.bid === block.id ? pending : null}
				onConsumed={() => (pending = null)}
			/>
		{/if}
	{/each}
</div>
