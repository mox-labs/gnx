<script lang="ts">
	// One document, folded to its section titles. A reader opens what they came for; printing,
	// or the browser's own find, opens the rest.
	import Prose from './Prose.svelte';
	import { document, sections } from './model';
	import { reader } from './reader.svelte';

	let { key, plain = false }: { key: string; plain?: boolean } = $props();

	const r = reader();
	const doc = $derived(document(r.book, key));
	const parts = $derived(sections(doc?.markdown ?? '').map((s, index) => ({ ...s, index })));
	const titled = $derived(parts.filter((s) => s.title));
	const allOpen = $derived(titled.length > 0 && titled.every((s) => r.open.has(`${key}/${s.index}`)));

	function toggle(index: number): void {
		const id = `${key}/${index}`;
		if (r.open.has(id)) r.open.delete(id);
		else r.open.add(id);
	}

	function toggleAll(): void {
		const open = allOpen;
		for (const s of titled) {
			if (open) r.open.delete(`${key}/${s.index}`);
			else r.open.add(`${key}/${s.index}`);
		}
	}

	// The browser's find reaches into a folded section and tells us, so we can unfold it.
	function found(el: HTMLElement, index: number) {
		const open = (): void => void r.open.add(`${key}/${index}`);
		el.addEventListener('beforematch', open);
		return { destroy: () => el.removeEventListener('beforematch', open) };
	}
</script>

{#if doc}
	<section class="layer" class:plain id="d-{key}">
		<header>
			<div>
				<p class="label">{r.book.directory}/{doc.file}</p>
				<h3>{doc.name}</h3>
				<p class="summary">{doc.summary}</p>
			</div>
			{#if titled.length > 1}
				<button type="button" class="all" aria-pressed={allOpen} onclick={toggleAll}>
					{allOpen ? 'Fold all' : 'Open all'}
				</button>
			{/if}
		</header>

		{#if doc.status !== 'PRESENT'}
			<div class="gaps">
				<p>
					<strong>{doc.status === 'ABSENT' ? 'Missing.' : 'Incomplete.'}</strong>
					<code>dao check</code> found:
				</p>
				<ul>
					{#each doc.gaps as gap, i (i)}<li>{gap}</li>{/each}
				</ul>
			</div>
		{/if}

		{#each parts as s (s.index)}
			{#if !s.title}
				<div class="preamble" id="s-{key}-{s.index}"><Prose text={s.body} shift={2} /></div>
			{:else}
				{@const open = r.open.has(`${key}/${s.index}`)}
				<div class="section" id="s-{key}-{s.index}">
					<h4>
						<button
							type="button"
							aria-expanded={open}
							aria-controls="b-{key}-{s.index}"
							onclick={() => toggle(s.index)}
						>
							<span class="chevron" aria-hidden="true"></span>
							<Prose text={s.title} inline />
						</button>
					</h4>
					<div
						class="body"
						id="b-{key}-{s.index}"
						hidden={open ? undefined : 'until-found'}
						use:found={s.index}
					>
						{#if s.body}
							<Prose text={s.body} shift={2} />
						{:else}
							<p class="empty">Nothing is written under this heading.</p>
						{/if}
					</div>
				</div>
			{/if}
		{/each}
	</section>
{/if}

<style>
	.layer {
		background: var(--dao-surface);
		border: 1px solid var(--dao-line);
		border-radius: 10px;
		padding: 1.25rem 1.4rem 0.6rem;
		scroll-margin-top: 1rem;
	}
	.layer.plain {
		background: none;
		border: 0;
		padding: 0;
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: start;
		gap: 1rem;
		margin-bottom: 0.9rem;
	}
	.label {
		margin: 0 0 0.3rem;
		font-family: var(--dao-mono);
		font-size: 0.74rem;
		color: var(--dao-muted);
	}
	h3 {
		margin: 0;
		font-family: var(--dao-serif);
		font-size: 1.3rem;
		font-weight: 600;
		line-height: 1.25;
	}
	.summary {
		margin: 0.3rem 0 0;
		font-size: 0.9rem;
		color: var(--dao-muted);
		max-width: 60ch;
	}
	.all {
		flex: none;
		font: inherit;
		font-size: 0.8rem;
		color: var(--dao-ink);
		background: none;
		border: 1px solid var(--dao-line-strong);
		border-radius: 999px;
		padding: 0.25em 0.8em;
		cursor: pointer;
	}
	.all:hover {
		border-color: var(--dao-ink);
	}
	.all:focus-visible,
	h4 button:focus-visible {
		outline: 2px solid var(--dao-accent);
		outline-offset: 2px;
	}
	.gaps {
		margin: 0 0 1rem;
		padding: 0.7rem 0.9rem;
		font-size: 0.875rem;
		border: 1px solid var(--dao-warn);
		border-radius: 8px;
	}
	.gaps p {
		margin: 0 0 0.3rem;
	}
	.gaps ul {
		margin: 0;
		padding-left: 1.2em;
	}
	.gaps code {
		font-family: var(--dao-mono);
		font-size: 0.9em;
	}
	.preamble {
		margin-bottom: 1rem;
		scroll-margin-top: 1rem;
	}
	.section {
		border-top: 1px solid var(--dao-line);
		scroll-margin-top: 1rem;
	}
	h4 {
		margin: 0;
		font-size: 1rem;
		font-weight: 600;
	}
	h4 button {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		width: 100%;
		text-align: left;
		font: inherit;
		color: var(--dao-ink);
		background: none;
		border: 0;
		border-radius: 4px;
		padding: 0.7rem 0;
		cursor: pointer;
	}
	h4 button :global(.prose) {
		font-family: var(--dao-sans);
	}
	h4 button:hover {
		color: var(--dao-accent);
	}
	.chevron {
		flex: none;
		width: 0.42em;
		height: 0.42em;
		border-right: 1.5px solid currentColor;
		border-bottom: 1.5px solid currentColor;
		transform: translateY(-0.12em) rotate(-45deg);
		transition: transform 120ms;
	}
	button[aria-expanded='true'] .chevron {
		transform: translateY(-0.2em) rotate(45deg);
	}
	.body {
		padding: 0 0 1.1rem 1.05rem;
	}
	.empty {
		margin: 0;
		font-size: 0.875rem;
		color: var(--dao-muted);
	}
	@media (prefers-reduced-motion: reduce) {
		.chevron {
			transition: none;
		}
	}
	@media print {
		.all {
			display: none;
		}
		.body[hidden] {
			display: block !important;
			content-visibility: visible !important;
		}
		.layer {
			break-inside: auto;
		}
	}
</style>
