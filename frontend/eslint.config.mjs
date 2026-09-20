import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTypescript from 'eslint-config-next/typescript';

const config = [
  ...nextVitals,
  ...nextTypescript,
  { ignores: ['.next/**', 'out/**', 'coverage/**', 'node_modules/**', 'next-env.d.ts'] },
];

export default config;
