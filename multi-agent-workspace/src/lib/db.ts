import { PrismaClient } from '@prisma/client';

// Singleton across Next.js dev-mode module reloads.
const g = globalThis as unknown as { __prisma?: PrismaClient };

export const prisma: PrismaClient =
  g.__prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === 'development' ? ['warn', 'error'] : ['error'],
  });

g.__prisma = prisma;
