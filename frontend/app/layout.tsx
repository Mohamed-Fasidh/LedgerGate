import './globals.css';
export const metadata = { title: 'LedgerGate', description: 'Enterprise financial verification and agent reliability' };
export default function RootLayout({children}:{children:React.ReactNode}) {
  return <html lang="en"><body>{children}</body></html>;
}
