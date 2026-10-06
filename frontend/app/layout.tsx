import './globals.css';
import './accounts.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Route 53 Console Clone',
  description: 'A Route 53 inspired hosted zone and DNS record management demo',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
