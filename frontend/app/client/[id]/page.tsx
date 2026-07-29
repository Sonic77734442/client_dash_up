import ClientDetailsPage from "./ClientDetailsPage";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ClientDetailsPage clientId={id} />;
}
