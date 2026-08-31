# Bucket du site : agregats publies et, plus tard, fichiers statiques.
#
# Separe de la couche brute, pour trois raisons :
#   - il sera expose au public via CloudFront, la couche brute jamais
#   - son contenu est entierement regenerable, donc pas de versionnement
#   - les droits different : la Lambda de transformation y ecrit, celle
#     de collecte n'y a aucun acces

resource "aws_s3_bucket" "site" {
  bucket = var.site_bucket_name
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  # Bloque meme si ce bucket sera public : l'acces se fera par CloudFront
  # avec Origin Access Control, jamais en direct. Un bucket S3 ouvert au
  # monde est une erreur classique — le CDN sert d'unique porte d'entree,
  # ce qui permet d'y attacher cache, journalisation et protections.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Pas de versionnement ici, volontairement : tout le contenu se regenere
# en relancant la transformation. Versionner doublerait le stockage sans
# apporter de securite reelle.

resource "aws_s3_bucket_lifecycle_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    id     = "nettoyer-uploads-incomplets"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
