// Side-effect CSS imports (maplibre-gl's stylesheet) have no types.
declare module "*.css";
// Vite worker URL import, used to tell MapLibre where its worker is (see configureMaps).
declare module "*?worker&url" {
  const url: string;
  export default url;
}
