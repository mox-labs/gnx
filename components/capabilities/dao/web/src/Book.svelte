<script lang="ts">
	// The book, once there is one to read: the front door, the map, and the rules.
	import { tick } from 'svelte';
	import Detail from './Detail.svelte';
	import Layer from './Layer.svelte';
	import Who from './Who.svelte';
	import Prose from './Prose.svelte';
	import { document, search, type Book, type Hit } from './model';
	import { provide, Reader, RULES } from './reader.svelte';

	let { book, root }: { book: Book; root: ShadowRoot | null } = $props();

	// A new book is a new component (the parent keys on it), so the reader is made once.
	// svelte-ignore state_referenced_locally
	const r = provide(new Reader(book));

	const front = $derived(document(book, 'front-door'));
	const wanting = $derived(book.documents.filter((d) => d.status !== 'PRESENT'));

	let query = $state('');
	const hits = $derived(search(book, query));

	function open(hit: Hit): void {
		if (hit.kind === 'position') r.select({ kind: 'position', name: hit.target });
		else r.showSection(hit.target, hit.index);
	}

	$effect(() => {
		const { id, n } = r.target;
		if (!n) return;
		void tick().then(() => {
			const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
			root?.getElementById(id)?.scrollIntoView({
				block: id === 'detail' ? 'nearest' : 'start',
				behavior: calm ? 'auto' : 'smooth'
			});
		});
	});
</script>

<article class="book" id="top">
	<header class="front">
		<p class="eyebrow">A directed agent organization · <code>{book.directory}/</code></p>
		<h1>{book.title}</h1>
		{#if front?.markdown}<Prose text={front.markdown} shift={2} />{/if}

		{#if book.complete}
			<p class="verdict good"><span class="dot"></span> <code>dao check</code> finds all six documents present.</p>
		{:else}
			<div class="verdict bad">
				<p><span class="dot"></span> <code>dao check</code> finds gaps in these documents.</p>
				<ul>
					{#each wanting as d (d.key)}
						<li>
							<strong>{d.name}</strong> <code>{d.file}</code>
							{#if d.gaps.length}
								<ul>
									{#each d.gaps as gap, i (i)}<li>{gap}</li>{/each}
								</ul>
							{/if}
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</header>

	<section class="part" id="who">
		<div class="heading">
			<h2>Who does what, and who reads their work</h2>
			<label class="find">
				<span>Find a word in the positions and the rules</span>
				<input type="search" bind:value={query} placeholder="release, merge, budget…" autocomplete="off" />
			</label>
		</div>

		{#if query.trim().length >= 2}
			<div class="hits" aria-live="polite">
				{#if hits.length}
					<ul>
						{#each hits as hit, i (i)}
							<li>
								<button type="button" onclick={() => open(hit)}>
									<span class="where">{hit.context} · <b>{hit.label}</b></span>
									<span class="words">{hit.before}<mark>{hit.match}</mark>{hit.after}</span>
								</button>
							</li>
						{/each}
					</ul>
				{:else}
					<p>Nothing in the positions or the rules says "{query.trim()}".</p>
				{/if}
			</div>
		{/if}

		<div class="who">
			<div class="map"><Who /></div>
			<Detail />
		</div>
	</section>

	<section class="part" id="rules">
		<div class="heading">
			<h2>The rules, at three levels</h2>
		</div>
		<div class="layers">
			{#each RULES as key (key)}<Layer {key} />{/each}
		</div>
	</section>

	<section class="part" id="written">
		<div class="heading">
			<h2>The positions and the participants, as written</h2>
		</div>
		<div class="layers">
			<Layer key="positions" />
			<Layer key="participants" />
		</div>
	</section>

	<footer>
		<p>
			This page is a copy. The source is the six Markdown documents in <code>{book.directory}/</code>;
			<code>dao book</code> makes the page from them and <code>dao check</code> reports when it has
			fallen behind.
		</p>
		{#if book.sources}<p class="digest">Made from documents with digest <code>{book.sources.slice(0, 12)}</code></p>{/if}
	</footer>
</article>

<style>
	.book {
		container: dao / inline-size;
		max-width: 76rem;
		margin: 0 auto;
		font-family: var(--dao-sans);
		font-size: 1rem;
		line-height: 1.55;
		color: var(--dao-ink);
	}
	code {
		font-family: var(--dao-mono);
		font-size: 0.88em;
	}
	.front {
		padding-bottom: 0.5rem;
	}
	.eyebrow {
		margin: 0 0 0.6rem;
		font-size: 0.78rem;
		letter-spacing: 0.04em;
		color: var(--dao-muted);
	}
	h1 {
		margin: 0 0 1rem;
		font-family: var(--dao-serif);
		font-size: clamp(1.9rem, 1.3rem + 2.4cqi, 3rem);
		font-weight: 600;
		line-height: 1.1;
		letter-spacing: -0.01em;
		text-wrap: balance;
	}
	.front :global(div.prose) {
		font-size: 1.08rem;
	}
	.verdict {
		margin: 1.4rem 0 0;
		font-size: 0.875rem;
	}
	.verdict p {
		margin: 0;
	}
	.verdict.bad {
		padding: 0.8rem 1rem;
		border: 1px solid var(--dao-bad);
		border-radius: 8px;
		max-width: 68ch;
	}
	.verdict ul {
		margin: 0.4rem 0 0;
		padding-left: 1.2em;
	}
	.verdict ul ul {
		margin-top: 0.15rem;
		color: var(--dao-muted);
	}
	.dot {
		display: inline-block;
		width: 0.55em;
		height: 0.55em;
		border-radius: 50%;
		margin-right: 0.3em;
		background: var(--dao-good);
	}
	.bad .dot {
		background: var(--dao-bad);
	}
	.part {
		margin-top: 3.25rem;
		scroll-margin-top: 1rem;
	}
	.heading {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: end;
		gap: 0.8rem 2rem;
		margin-bottom: 1.4rem;
		padding-bottom: 0.7rem;
		border-bottom: 1px solid var(--dao-line-strong);
	}
	h2 {
		margin: 0;
		font-size: 0.82rem;
		font-weight: 650;
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}
	.find {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.75rem;
		color: var(--dao-muted);
		flex: 0 1 22rem;
	}
	.find input {
		font: inherit;
		font-size: 0.925rem;
		color: var(--dao-ink);
		background: var(--dao-surface);
		border: 1px solid var(--dao-line-strong);
		border-radius: 8px;
		padding: 0.45rem 0.7rem;
		min-width: 0;
	}
	.find input:focus-visible {
		outline: 2px solid var(--dao-accent);
		outline-offset: 1px;
	}
	.hits {
		margin-bottom: 1.5rem;
		font-size: 0.9rem;
	}
	.hits p {
		margin: 0;
		color: var(--dao-muted);
	}
	.hits ul {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--dao-line);
		border-radius: 10px;
		background: var(--dao-surface);
		overflow: hidden;
	}
	.hits li + li {
		border-top: 1px solid var(--dao-line);
	}
	.hits button {
		display: grid;
		grid-template-columns: minmax(9rem, 16rem) minmax(0, 1fr);
		gap: 0.2rem 1rem;
		width: 100%;
		text-align: left;
		font: inherit;
		color: var(--dao-ink);
		background: none;
		border: 0;
		padding: 0.6rem 0.9rem;
		cursor: pointer;
	}
	.hits button:hover {
		background: var(--dao-sunk);
	}
	.hits button:focus-visible {
		outline: 2px solid var(--dao-accent);
		outline-offset: -2px;
	}
	.where {
		color: var(--dao-muted);
		font-size: 0.82rem;
	}
	.where b {
		color: var(--dao-ink);
		font-weight: 600;
	}
	.words {
		overflow-wrap: anywhere;
	}
	mark {
		color: inherit;
		background: var(--dao-mark);
		border-radius: 2px;
		padding: 0 0.1em;
	}
	.who {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 2rem;
		align-items: start;
	}
	.map {
		min-width: 0;
	}
	.layers {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	footer {
		margin-top: 3.25rem;
		padding-top: 1rem;
		border-top: 1px solid var(--dao-line);
		font-size: 0.8rem;
		color: var(--dao-muted);
	}
	footer p {
		margin: 0 0 0.3rem;
		max-width: 76ch;
	}
	@container dao (min-width: 58rem) {
		.who {
			grid-template-columns: minmax(0, 11fr) minmax(0, 9fr);
		}
		.who :global(.detail) {
			position: sticky;
			top: 1rem;
		}
	}
	@container dao (max-width: 34rem) {
		.hits button {
			grid-template-columns: 1fr;
		}
	}
	@media print {
		.find,
		.hits {
			display: none;
		}
		.part {
			margin-top: 2rem;
		}
	}
</style>
