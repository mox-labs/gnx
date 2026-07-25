/**
 * Frontmatter guard — structural, not editorial.
 *
 * Three failure classes, all of which shipped undetected before this existed:
 *   1. UNKNOWN   — a key nothing parses. Pure fluff; it round-trips silently.
 *   2. INVALID   — a key that parses but whose value fails its type guard, so it
 *                  silently becomes `undefined` (e.g. `status: drafted`).
 *   3. REDUNDANT — a key restating what another key already determines
 *                  (`mode: explanation` on a doc in section `explanation`).
 *
 * Plus one advisory:
 *   4. ORPHAN    — a field content.ts parses that no component or route renders.
 *                  A field with no consumer is fluff by definition. Fix by
 *                  rendering it or deleting it — not by documenting it.
 *
 * The schema is DERIVED from content.ts (unions + DocMeta fields), never restated
 * here, so this cannot drift from the parser it guards.
 *
 * Run: bun run check:docs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const EXPERIENCE = path.resolve(HERE, '..');
const CONTENT_TS = path.join(EXPERIENCE, 'src/lib/server/content.ts');
const CONTENT_DIR = path.resolve(EXPERIENCE, '../content');
const SRC_DIR = path.join(EXPERIENCE, 'src');

const src = readFileSync(CONTENT_TS, 'utf8');

/** Extract `export type X = 'a' | 'b';` → ['a','b'] */
function union(name: string): string[] {
	const m = src.match(new RegExp(`export type ${name}\\s*=\\s*([^;]+);`));
	return m ? [...m[1].matchAll(/'([^']+)'/g)].map((x) => x[1]) : [];
}

/** Field names declared on the DocMeta interface. */
function docMetaFields(): string[] {
	const m = src.match(/export interface DocMeta \{([\s\S]*?)\n\}/);
	if (!m) throw new Error('DocMeta not found in content.ts — checker needs updating');
	return [...m[1].matchAll(/^\s*(\w+)\??:/gm)].map((x) => x[1]);
}

const STATUS = union('Status');
const MODE = union('Mode');
const SECTION = union('Section');
const REGISTER = union('Register');
// slug/order/file are derived from the path, never authored in frontmatter.
const DERIVED = new Set(['slug', 'order', 'file']);
const ALLOWED = docMetaFields().filter((f) => !DERIVED.has(f));

const ENUMS: Record<string, string[]> = {
	status: STATUS,
	mode: MODE,
	section: SECTION,
	register: REGISTER
};

function walk(dir: string, out: string[] = []): string[] {
	for (const e of readdirSync(dir)) {
		const full = path.join(dir, e);
		if (statSync(full).isDirectory()) walk(full, out);
		else if (e.endsWith('.md')) out.push(full);
	}
	return out;
}

/** Mirrors content.ts's parser exactly: flat `key: value` between --- fences. */
function frontmatter(file: string): Record<string, string> | null {
	const m = readFileSync(file, 'utf8').match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
	if (!m) return null;
	const fm: Record<string, string> = {};
	for (const line of m[1].split(/\r?\n/)) {
		const kv = line.match(/^(\w[\w-]*):\s*(.*)$/);
		if (kv) fm[kv[1]] = kv[2].trim();
	}
	return fm;
}

const problems: string[] = [];
const files = walk(CONTENT_DIR);

for (const file of files) {
	const rel = path.relative(CONTENT_DIR, file);
	const fm = frontmatter(file);
	if (!fm) continue; // no frontmatter is legal — title falls back to the H1

	for (const [key, value] of Object.entries(fm)) {
		if (!ALLOWED.includes(key)) {
			problems.push(`UNKNOWN    ${rel}: '${key}' — nothing parses this. Delete it.`);
			continue;
		}
		const allowed = ENUMS[key];
		if (allowed && allowed.length && !allowed.includes(value)) {
			problems.push(
				`INVALID    ${rel}: ${key}: '${value}' — not one of ${allowed.join('|')}. Fails its type guard and silently becomes undefined.`
			);
		}
	}

	const dirSection = path.dirname(rel).split(path.sep)[0];
	if (fm.mode && (fm.mode === fm.section || fm.mode === dirSection)) {
		problems.push(
			`REDUNDANT  ${rel}: mode: '${fm.mode}' restates the section. Drop it — the chip would say what the nav already says.`
		);
	}
}

// ORPHAN sweep — a parsed field that nothing outside content.ts renders.
const consumers = walk(SRC_DIR, [])
	.concat(
		(function collect(dir: string, out: string[] = []): string[] {
			for (const e of readdirSync(dir)) {
				const full = path.join(dir, e);
				if (statSync(full).isDirectory()) collect(full, out);
				else if (/\.(svelte|ts)$/.test(e) && full !== CONTENT_TS) out.push(full);
			}
			return out;
		})(SRC_DIR)
	)
	.filter((f) => /\.(svelte|ts)$/.test(f) && f !== CONTENT_TS);

const consumerSrc = [...new Set(consumers)].map((f) => readFileSync(f, 'utf8')).join('\n');
const orphans = ALLOWED.filter(
	(f) => !['title', 'section', 'register'].includes(f) && !new RegExp(`\\.${f}\\b`).test(consumerSrc)
);

for (const f of orphans) {
	problems.push(
		`ORPHAN     content.ts parses '${f}' but nothing renders it. Render it or delete the field.`
	);
}

const hard = problems.filter((p) => !p.startsWith('ORPHAN'));
if (problems.length === 0) {
	console.log(`frontmatter: ${files.length} docs, clean.`);
	process.exit(0);
}
for (const p of problems) console.log(p);
console.log(
	`\n${files.length} docs · ${hard.length} error${hard.length === 1 ? '' : 's'} · ${problems.length - hard.length} advisory`
);
process.exit(hard.length ? 1 : 0);
