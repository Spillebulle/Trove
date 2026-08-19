/// <reference types="vite/client" />

// noVNC 1.7 exports its RFB client from the package root (`exports:
// ./core/rfb.js`), while DefinitelyTyped still describes it under the old
// `lib/rfb` path. Point the one at the other.
declare module '@novnc/novnc' {
  import RFB from '@novnc/novnc/lib/rfb'
  export default RFB
}
