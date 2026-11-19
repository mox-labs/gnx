// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}

	// The deploy base, set by vite.config.ts (`define`). Read it through #lib/base.ts.
	const __DEPLOY_BASE__: string;
}

export {};
