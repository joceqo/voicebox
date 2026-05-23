import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
  stories: [
    '../src/**/*.mdx',
    '../src/**/*.stories.@(ts|tsx)',
  ],
  addons: [
    '@storybook/addon-docs',
    '@storybook/addon-a11y',
    '@storybook/addon-themes',
    '@storybook/addon-vitest',
  ],
  framework: {
    name: '@storybook/react-vite',
    options: {},
  },
  typescript: {
    reactDocgen: 'react-docgen-typescript',
  },
  // Bun resolves some Storybook internals (notably the MDX react shim) to
  // `file://` URLs that Rollup can't bundle. Rewriting them to plain absolute
  // paths in a `resolveId` hook lets the preview build pass through unchanged.
  viteFinal: async (config) => {
    config.plugins = config.plugins ?? [];
    config.plugins.push({
      name: 'voicebox-strip-file-url',
      enforce: 'pre',
      resolveId(source) {
        if (source.startsWith('file://')) {
          return source.slice('file://'.length);
        }
        return null;
      },
    });
    return config;
  },
};

export default config;
