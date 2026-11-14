<svelte:options customElement={{ props: { src: { type: 'String' }, book: { type: 'Object' } } }} />

<script lang="ts">
	// <dao-book>: a project's dao, explorable.
	//
	// It reads the book `dao book` writes, from the first of: the `book` property, a
	// <script type="application/json"> child, or the file the `src` attribute names. It loads
	// nothing else, and shows the documents' words through one renderer that cannot act.
	import Book from './Book.svelte';
	import { parse, read, type Book as Data } from './model';

	let { src = '', book = undefined }: { src?: string; book?: unknown } = $props();

	let data = $state<Data | null>(null);
	let failure = $state('');
	// What stopped the book being drawn, when something in it did.
	let broken = $state('');
	const root = $host().shadowRoot;

	const said = (e: unknown): string => (e instanceof Error ? e.message : String(e));

	$effect(() => {
		data = null;
		failure = '';
		if (book != null) {
			try {
				data = parse(book);
			} catch (e) {
				failure = said(e);
			}
			return;
		}
		const carried = $host().querySelector(':scope > script[type="application/json"]')?.textContent;
		if (carried) {
			try {
				data = read(carried);
			} catch (e) {
				failure = said(e);
			}
			return;
		}
		if (!src) {
			failure = 'No book was given. Set src to the book.json that `dao book` writes.';
			return;
		}
		const stop = new AbortController();
		fetch(src, { signal: stop.signal, credentials: 'same-origin' })
			.then((response) => {
				if (!response.ok) throw new Error(`${src} could not be read (${response.status}).`);
				return response.text();
			})
			.then((text) => (data = read(text)))
			.catch((e) => {
				if (!stop.signal.aborted) failure = said(e);
			});
		return () => stop.abort();
	});
</script>

{#if data}
	{#key data}
		<svelte:boundary onerror={(e) => (broken = said(e))}>
			<Book book={data} {root} />
			{#snippet failed()}
				<p class="state" role="alert">
					The book could not be drawn ({broken}). Everything it would show is in the Markdown
					documents beside it.
				</p>
			{/snippet}
		</svelte:boundary>
	{/key}
{:else if failure}
	<p class="state" role="alert">{failure}</p>
{:else}
	<p class="state">Reading the book…</p>
{/if}

<style>
	:host {
		display: block;
		box-sizing: border-box;
		padding: clamp(1rem, 4vw, 3rem) clamp(1rem, 4vw, 2.5rem);
		background: var(--dao-bg);
		color-scheme: light dark;

		--dao-sans: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
		--dao-serif: Charter, 'Bitstream Charter', 'Iowan Old Style', 'Sitka Text', Cambria, Georgia, serif;
		--dao-mono: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;

		--dao-bg: #f5f6f9;
		--dao-surface: #ffffff;
		--dao-sunk: #eceef3;
		--dao-ink: #161a25;
		--dao-muted: #586074;
		--dao-line: #dfe2ea;
		--dao-line-strong: #b9bfcd;
		--dao-accent: #2b47d6;
		--dao-mark: #ffe58a;
		--dao-human: #b4530a;
		--dao-agent: #0c7a6e;
		--dao-good: #1b7a45;
		--dao-warn: #975400;
		--dao-bad: #b3261e;
	}
	@media (prefers-color-scheme: dark) {
		:host(:not([theme='light'])) {
			--dao-bg: #0e1117;
			--dao-surface: #161a23;
			--dao-sunk: #1f2430;
			--dao-ink: #e8ebf2;
			--dao-muted: #9aa3b6;
			--dao-line: #272d3a;
			--dao-line-strong: #434b5d;
			--dao-accent: #93a6ff;
			--dao-mark: #6b5600;
			--dao-human: #f0a35c;
			--dao-agent: #4fd1c0;
			--dao-good: #6fd49a;
			--dao-warn: #e6b055;
			--dao-bad: #ff8a80;
		}
	}
	:host([theme='dark']) {
		--dao-bg: #0e1117;
		--dao-surface: #161a23;
		--dao-sunk: #1f2430;
		--dao-ink: #e8ebf2;
		--dao-muted: #9aa3b6;
		--dao-line: #272d3a;
		--dao-line-strong: #434b5d;
		--dao-accent: #93a6ff;
		--dao-mark: #6b5600;
		--dao-human: #f0a35c;
		--dao-agent: #4fd1c0;
		--dao-good: #6fd49a;
		--dao-warn: #e6b055;
		--dao-bad: #ff8a80;
	}
	:host([theme='light']) {
		color-scheme: light;
	}
	:host([theme='dark']) {
		color-scheme: dark;
	}
	.state {
		margin: 0;
		font-family: var(--dao-sans);
		color: var(--dao-muted);
	}
	@media print {
		:host {
			padding: 0;
			background: none;
		}
	}
</style>
