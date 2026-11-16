export function FakeGenerationBanner({ active }: { active: boolean }) {
  return active ? <p role="alert" className="error"><strong>Test mode: this mesh was not generated from your photo.</strong></p> : null;
}
