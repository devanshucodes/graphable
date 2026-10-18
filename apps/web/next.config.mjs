/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // STATIC_EXPORT=1 writes plain HTML/JS to out/ for a static host (the hosted demo): every page renders in the
  // browser and calls the API at NEXT_PUBLIC_API_URL, so no Node server is needed.
  ...(process.env.STATIC_EXPORT === "1" ? { output: "export", trailingSlash: true } : {}),
};
export default nextConfig;
