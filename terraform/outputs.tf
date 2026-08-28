# Sorties : les valeurs qu'on veut recuperer apres un apply.
#
# Utile pour deux raisons : les consulter rapidement avec
# "terraform output", et les consommer depuis un script ou une chaine
# de deploiement sans les recopier a la main.

output "raw_bucket_name" {
  description = "Nom du bucket de la couche brute."
  value       = aws_s3_bucket.raw.id
}

output "raw_bucket_arn" {
  description = "ARN du bucket, necessaire pour les politiques IAM des Lambdas."
  value       = aws_s3_bucket.raw.arn
}

output "aws_region" {
  description = "Region de deploiement."
  value       = var.aws_region
}
