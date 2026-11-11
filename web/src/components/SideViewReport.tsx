export type SideViewResult = {
  status: 'pass' | 'warn' | 'unavailable'; message: string; fractions: number[];
  mean_relative_difference?: number; max_relative_difference?: number;
  mesh_width_over_length?: number[];
  photo_profiles?: { side: { width_over_length: number[] } };
  orientation?: { candidate_main_angles_degrees: number[] };
};

export function SideViewReport({ report }: { report?: SideViewResult }) {
  if (!report) return <p className="field-help">Side comparison unavailable: this run has no comparison report.</p>;
  const photo = report.photo_profiles?.side.width_over_length;
  const mesh = report.mesh_width_over_length;
  const max = Math.max(...(photo ?? []), ...(mesh ?? []), .01);
  const points = (values: number[]) => values.map((v, i) => `${40 + report.fractions[i] * 320},${165 - v / max * 130}`).join(' ');
  return <section className="side-report" aria-label="Side-view consistency report">
    <h3>Side-view consistency · {report.status === 'warn' ? 'review needed' : report.status === 'pass' ? 'no mismatch flagged' : 'unavailable'}</h3>
    <p className="field-help">{report.message}</p>
    {report.mean_relative_difference != null && report.max_relative_difference != null && <p className="field-help">Estimated-view width difference: mean {(report.mean_relative_difference * 100).toFixed(1)}% · largest {(report.max_relative_difference * 100).toFixed(1)}% of the photo width.</p>}
    {photo && mesh && <><svg viewBox="0 0 400 205" role="img" aria-label="Normalized width profiles: side photo in green, mesh in orange" style={{ width: '100%', maxWidth: 500 }}>
      <line x1="40" y1="165" x2="360" y2="165" stroke="#66776a"/><line x1="40" y1="25" x2="40" y2="165" stroke="#66776a"/>
      <polyline points={points(photo)} fill="none" stroke="#23583c" strokeWidth="3"/><polyline points={points(mesh)} fill="none" stroke="#b45b19" strokeWidth="3" strokeDasharray="6 3"/>
      <text x="40" y="190" fontSize="12">Start</text><text x="340" y="190" fontSize="12">Tip</text><text x="45" y="18" fontSize="12">Width ÷ limb length</text>
    </svg><p className="field-help">Green: side photo. Dashed orange: mesh at the direction estimated from the main photo. Profiles run from 5% to 95% of length.</p></>}
    <p className="field-help">Warning only: the mesh is unchanged. Camera pose and thresholds are provisional; this does not verify accuracy or fit. Full profiles and orientation uncertainty are in validation.json.</p>
  </section>;
}
