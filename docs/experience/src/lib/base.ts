// The path the site is deployed under: '' or '/gnx', with no trailing slash. vite.config.ts
// sets it, and gives SvelteKit the same value as paths.base.
//
// Use it where a link has to be absolute: text an agent reads (llms.txt, the .md twins) and
// links rewritten inside rendered markdown. In a component, call resolve() from $app/paths,
// which gives the page a relative link while it is rendered on the server.
export const DEPLOY_BASE: string = __DEPLOY_BASE__;
