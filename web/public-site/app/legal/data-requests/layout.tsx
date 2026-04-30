import type { Metadata } from 'next';
import { SITE_NAME } from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Data Access Requests - ${SITE_NAME}`,
  description: 'Submit your GDPR data access, deletion, or portability request',
};

export default function DataRequestsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
