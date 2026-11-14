<script lang="ts">
	// What the reader picked, in full. With nothing picked, the organization in a few lines.
	import Mark from './Mark.svelte';
	import Name from './Name.svelte';
	import Prose from './Prose.svelte';
	import { participant, position } from './model';
	import { reader } from './reader.svelte';

	const r = reader();

	const post = $derived(r.selected?.kind === 'position' ? position(r.book, r.selected.name) : undefined);
	const person = $derived(
		r.selected?.kind === 'participant' ? participant(r.book, r.selected.name) : undefined
	);
	const held = $derived(
		(person?.positions ?? []).map((name) => position(r.book, name)).filter((p) => p !== undefined)
	);
	const people = $derived(r.book.participants.filter((p) => p.kind === 'human').length);
	const agents = $derived(r.book.participants.filter((p) => p.kind === 'agent').length);
	const count = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;
</script>

{#snippet names(kind: 'position' | 'participant', list: string[], none: string)}
	{#if list.length}
		<span class="names">{#each list as name (name)}<Name {kind} {name} />{/each}</span>
	{:else}
		<span class="nobody">{none}</span>
	{/if}
{/snippet}

<section class="detail" id="detail" aria-live="polite">
	{#if post}
		<header>
			<p class="label">Position</p>
			<h3>{post.name}</h3>
			<button type="button" class="clear" onclick={() => (r.selected = null)}>Back to overview</button>
		</header>
		<dl>
			<dt>Held by</dt>
			<dd>{@render names('participant', post.held_by, 'No one holds this position.')}</dd>
			<dt>Does</dt>
			<dd>
				{#if post.does}<Prose text={post.does} />{:else}<span class="nobody">Not stated.</span>{/if}
			</dd>
			<dt>Must not</dt>
			<dd>
				{#if post.must_not}<Prose text={post.must_not} />{:else}<span class="nobody">Not stated.</span>{/if}
			</dd>
			<dt>Checked by</dt>
			<dd>
				{#if post.checked_by.length}
					{@render names('position', post.checked_by, '')}
				{:else if post.held_only_by_people}
					<span class="nobody">No position. Its holders are people, and the checking ends with them.</span>
				{:else}
					<span class="nobody warn">No one reads this position's work.</span>
				{/if}
			</dd>
			<dt>Reads the work of</dt>
			<dd>{@render names('position', post.checks, 'No other position.')}</dd>
		</dl>
	{:else if person}
		<header>
			<p class="label">Participant</p>
			<h3><Mark kind={person.kind} /> {person.name}</h3>
			<button type="button" class="clear" onclick={() => (r.selected = null)}>Back to overview</button>
		</header>
		<dl>
			<dt>Is</dt>
			<dd>
				{person.kind === 'human' ? 'A person.' : person.kind === 'agent' ? 'An agent.' : 'Not stated as a person or an agent.'}
				{#if person.defined_in}Defined in <code>{person.defined_in}</code>.{/if}
			</dd>
			<dt>Holds</dt>
			<dd>{@render names('position', person.positions, 'No position.')}</dd>
		</dl>
		{#each held as p (p.name)}
			<div class="remit">
				<p class="label">As {p.name}</p>
				<dl>
					<dt>Does</dt>
					<dd>
						{#if p.does}<Prose text={p.does} />{:else}<span class="nobody">Not stated.</span>{/if}
					</dd>
					<dt>Must not</dt>
					<dd>
						{#if p.must_not}<Prose text={p.must_not} />{:else}<span class="nobody">Not stated.</span>{/if}
					</dd>
					<dt>Checked by</dt>
					<dd>{@render names('position', p.checked_by, 'No position.')}</dd>
				</dl>
			</div>
		{/each}
	{:else}
		<header>
			<p class="label">Overview</p>
			<h3>
				{count(r.book.participants.length, 'participant holds', 'participants hold')}
				{count(r.book.positions.length, 'position', 'positions')}
			</h3>
		</header>
		<p class="lede">
			{count(people, 'person', 'people')} and {count(agents, 'agent', 'agents')}. Pick anyone, or any
			position, to see what they do, what they must not do, and who reads their work.
		</p>
		{#if r.book.chains.length}
			<p class="label chains-label">Where the checking runs</p>
			<ul class="chains">
				{#each r.book.chains as chain, i (i)}
					<li>
						{#each chain as name, j (j)}
							{#if j}<span class="to" aria-label="is checked by">→</span>{/if}
							<Name kind="position" {name} />
						{/each}
					</li>
				{/each}
			</ul>
			<p class="hint">Each arrow reads "is checked by". A chain ends where no position checks the last.</p>
		{/if}
	{/if}
</section>

<style>
	.detail {
		background: var(--dao-surface);
		border: 1px solid var(--dao-line);
		border-radius: 10px;
		padding: 1.25rem 1.4rem 1.4rem;
		min-width: 0;
		scroll-margin-top: 1rem;
	}
	header {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: baseline;
		gap: 0 1rem;
		margin-bottom: 0.9rem;
	}
	header .label {
		grid-column: 1 / -1;
	}
	h3 {
		margin: 0;
		font-family: var(--dao-serif);
		font-size: 1.3rem;
		font-weight: 600;
		line-height: 1.25;
		text-wrap: balance;
		display: flex;
		align-items: center;
		gap: 0.4em;
	}
	.label {
		margin: 0 0 0.3rem;
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--dao-muted);
	}
	.clear {
		font: inherit;
		font-size: 0.8rem;
		color: var(--dao-accent);
		background: none;
		border: 0;
		padding: 0;
		cursor: pointer;
		text-decoration: underline;
		text-underline-offset: 0.15em;
	}
	.clear:focus-visible {
		outline: 2px solid var(--dao-accent);
		outline-offset: 2px;
	}
	dl {
		display: grid;
		grid-template-columns: minmax(5.5rem, max-content) minmax(0, 1fr);
		gap: 0.8rem 1.1rem;
		margin: 0;
	}
	dt {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--dao-muted);
		padding-top: 0.15rem;
	}
	dd {
		margin: 0;
		min-width: 0;
	}
	code {
		font-family: var(--dao-mono);
		font-size: 0.86em;
		background: var(--dao-sunk);
		padding: 0.1em 0.3em;
		border-radius: 3px;
		overflow-wrap: anywhere;
	}
	.names {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.nobody {
		color: var(--dao-muted);
		font-size: 0.925rem;
	}
	.nobody.warn {
		color: var(--dao-warn);
	}
	.remit {
		margin-top: 1.2rem;
		padding-top: 1.1rem;
		border-top: 1px solid var(--dao-line);
	}
	.lede {
		margin: 0;
		color: var(--dao-muted);
		max-width: 52ch;
	}
	.chains-label {
		margin-top: 1.3rem;
	}
	.chains {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.55rem;
	}
	.chains li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
	}
	.to {
		color: var(--dao-accent);
	}
	.hint {
		margin: 0.8rem 0 0;
		font-size: 0.78rem;
		color: var(--dao-muted);
	}
	@media (max-width: 30rem) {
		dl {
			grid-template-columns: 1fr;
			gap: 0.2rem;
		}
		dd {
			margin-bottom: 0.7rem;
		}
	}
</style>
