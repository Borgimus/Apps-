/** @type {import('next').NextConfig} */
const nextConfig = {
  serverExternalPackages: ['@prisma/client'],
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
