import ClientDetailsPage from "./ClientDetailsPage";

export default function Page({ params }: { params: { id: string } }) {
  return <ClientDetailsPage clientId={params.id} />;
}
