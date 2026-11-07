// The "start here by goal" paths: what a reader came to do → the component that does it.
// One list, read by the landing page and by llms.txt, so the two cannot drift.
// `path` is site-relative; callers prefix the deploy base.

export interface Goal {
	goal: string;
	name: string;
	path: string;
}

export const GOALS: Goal[] = [
	{ goal: 'Decide whether an agent change helped', name: 'ix', path: '/components/ix' },
	{ goal: 'Collect sources, query them with SQL', name: 'recon', path: '/components/recon' },
	{
		goal: 'Compose components and agents, every row recorded',
		name: 'matrix',
		path: '/components/matrix'
	},
	{ goal: 'Call any model family through one port', name: 'hardline', path: '/components/hardline' },
	{ goal: "Audit a project's agent organization", name: 'dao', path: '/components/dao' },
	{ goal: 'Add skills to Claude Code', name: 'plugins', path: '/catalog#plugins' }
];
