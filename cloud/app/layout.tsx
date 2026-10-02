import type {ReactNode} from 'react';
import './style.css';
export const metadata={title:'ClipRank — Studio Control',description:'Your local content studio, managed remotely.'};
export default function Layout({children}:{children:ReactNode}){return <html lang="en"><body>{children}</body></html>;}
